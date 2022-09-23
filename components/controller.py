import asyncio
from prisma import Prisma
import socket
import time
import sys
from threading import Thread
import logging
from datetime import datetime, timedelta
import functools


INPUT_LABELS = "INPUT LABELS:"
PROTOCOL_PREAMPLE = "PROTOCOL PREAMPLE:"
PROTOCOL_VIDEOHUB_DEVICE = "VIDEOHUB DEVICE:"
OUTPUT_START = "OUTPUT LABELS:"
VIDEO_OUTPUT_ROUTING = "VIDEO OUTPUT ROUTING:"
FRIENDLY_NAME = "Friendly name:"

PROTOCOL_LATEST_VERSION = "2.8"

CONNECTION_RETRY_DELAY = 2

videohubs = []
prisma = Prisma()
loop = asyncio.get_event_loop()

class Input:
    def __init__(self, id, label):
        self.id = int(id)
        self.label = label

class Output:
    def __init__(self, id, label):
        self.id = int(id)
        self.label = label
        self.input_id = None


class VideohubClient(asyncio.Protocol):
    message = 'Testing'

    def __init__(self, hub):
        self.hub = hub

    def info(self, msg):
        self.hub.info(msg)

    def connection_made(self, transport):
        self.transport = transport
        self.transport.write(b'Hello!')
        self.hub.connected = True
        self.hub.sock = self
        self.info("Connected.")
        #server_udp[1].tcp_client_connected()


    def data_received(self, data):
        data = data.decode('utf-8')
        self.info(f"Received: {data!r}")
        loop.create_task(self.hub.handle_received(data))
        #if self.data == 'Testing':
            #server_udp[1].send_data_to_udp(self.data)

    def send_data_to_tcp(self, data):
        self.transport.write(data.encode())
        self.info(f"Sending: {data}")

    def connection_lost(self, exc):
        #info = self.transport.get_extra_info('peername')
        self.hub.connected = False
        self.info("Connection lost. Attempting reconnect.")
        self.hub.connect()
        #server_udp[1].tcp_client_disconnected(msg, info)


class Videohub():
    def __init__(self, id, ip):
        self.id = int(id)
        self.ip = ip
        self.name = "undefined"
        self.version = PROTOCOL_LATEST_VERSION
        self.inputs = [] # in case hub does not send initial config
        self.outputs = []
        self.connected = False
        self.sock = None

    async def load_initial(self, text):
        self.info("Loading initial data.")
        lines = getLines(text)

        # ver and name
        self.name = getConfigEntry(lines, 3)

        # inputs and outputs
        self.inputs = []
        lines_section = getCorrespondingLines(lines, INPUT_LABELS)

        for i in range(len(lines_section)):
            line = lines_section[i]
            index = int(line.index(" "))

            self.inputs.append(Input(int(line[0 : (index)]), line[(index + 1):]))

        self.outputs = []
        lines_section = getCorrespondingLines(lines, OUTPUT_START)
        for i in range(len(lines_section)):
            line = lines_section[i]
            index = line.index(" ")
            self.outputs.append(Output(int(line[0: index]), line[(index + 1):]))

        # mapping
        lines_section = getCorrespondingLines(lines, VIDEO_OUTPUT_ROUTING)

        for i in range(len(lines_section)):
            line = lines_section[i]
            data = line.split(" ")
            self.outputs[int(data[0])].input_id = int(data[1])

        await self.save()
        self.info("Initial data loaded.")

        #self.send_routing_update(self.outputs[0].id, self.inputs[1].id)


    async def handle_received(self, data):
        if data.startswith(PROTOCOL_PREAMPLE): # preample:
            lines = getCorrespondingLines(getLines(data), PROTOCOL_PREAMPLE)
            self.version = lines[1]

        if data.startswith(PROTOCOL_VIDEOHUB_DEVICE): # initial
            await self.load_initial(data)
        elif data.startswith(VIDEO_OUTPUT_ROUTING): # update routing
            lines = getCorrespondingLines(getLines(data), VIDEO_OUTPUT_ROUTING)
            i = 0

            for i in range(len(lines)):
                line = lines[i]
                data = line.split(" ")
                output_id = int(data[0])
                input_id = int(data[1])

                await self.update_routing(output_id, input_id)
        else:
            self.info("Unknown message.")


    def validate_relation(self, input_id, output_id):
        if output_id >= len(self.outputs):
            self.info(f"Output not loaded. Id: {output_id}")
            return False

        if input_id >= len(self.inputs):
            self.info(f"Input not loaded: Id: {input_id}")
            return False

        return True

    async def update_routing(self, output_id, input_id):
        self.info(f"Updating routing in database: ({output_id}, {input_id})")

        if not self.validate_relation(input_id, output_id):
            return False

        self.outputs[output_id].input_id = input_id
        await prisma.output.update(
            where={
                'videohubOutput': {
                    'videohub_id': self.id,
                    'id': output_id,
                }
            },
            data={
                'input_id' : input_id,
            },
        )

        self.info(f"Routing in db updated: ({output_id}, {input_id})")
        return True

    def connect_initial(self):
        self.connect()
        loop.create_task(self.check_events())
        self.info("Inital tasks started.")

    def connect(self):
        try:
            loop.create_task(self.socket_connect())
        except:
            logging.exception("Failed to connect.")

    @asyncio.coroutine
    def socket_connect(self):
        if self.connected: # prevent error
            raise Exception(f"[{self.id}] Tried to connect, but already is.")
            return

        c = 1
        while True:
            try:
                self.info(f"Trying to connect (#{c})")
                call = functools.partial(VideohubClient, hub=self)
                yield from loop.create_connection(call, self.ip, 9990)
                self.connected = True
                self.info("Establishing connection...")
            except OSError:
                logging.exception(f"[{self.id}] Couldn't connect to socket. Trying again in {CONNECTION_RETRY_DELAY} second(s).")
                yield from asyncio.sleep(CONNECTION_RETRY_DELAY)
                c += 1
            else:
                break

    def establish_connection(self, sock, c):
        self.info(f"Attempting connection (#{c}).")
        sock.connect((self.ip, 9990))
        self.info("Connected to socket.")

    async def send_routing_update(self, output, input):
        send = f"{VIDEO_OUTPUT_ROUTING}\n{output} {input}\n"
        self.info(f"Sending routing update: {output}:{input}")
        if not self.validate_relation(output, input):
            return False

        try:
            self.sock.send_data_to_tcp(send)
            self.info("Routing update sent.")
        except socket.error:
            logging.exception(f"[{self.id}] Couldn't send routing update to videohub.")

        return await self.update_routing(output, input)

    def info(self, msg):
        m = f"[{self.id}] {msg}"
        logging.info(m)
        print(m)

    async def save(self):
        self.info("Saving.")

        try:
            await prisma.videohub.update(
                where={
                    'id': self.id,
                },
                data={
                    'name': self.name,
                }
            )

            for input in self.inputs:
                id = input.id
                inp = await prisma.input.upsert(
                    where={
                        'videohubInput': {
                            'videohub_id': self.id,
                            'id': id,
                        }
                    },
                    data={
                        'create' : {
                            'id' : id,
                            'videohub_id' : self.id,
                            'label' : input.label,
                        },
                        'update' : {
                            'label' : input.label,
                        },
                    },
                )

            for output in self.outputs:
                out = await prisma.output.upsert(
                    where={
                            'videohubOutput': {
                                'videohub_id': self.id,
                                'id': output.id,
                            }
                    },
                    data={
                        'update' : {
                            'input_id' : output.input_id,
                            'label' : output.label,
                        },
                        'create' : {
                            'id' : output.id,
                            'videohub_id' : self.id,
                            'input_id' : output.input_id,
                            'label' : output.label,
                        },
                    },
                )

            self.info("Saved.")
        except:
            logging.exception(f"[{self.id}] Failed to save inputs or outputs.")

    async def check_events(self) -> None:
        date_start = datetime.utcnow()
        date_end = date_start + timedelta(minutes=1)
        try:
            while True:
                if self.connected:
                    events = await prisma.event.find_many(
                        where= {
                            'AND': [
                                {
                                    'videohub_id': self.id
                                },
                                {
                                    'OR': [
                                        {
                                            'AND': [
                                                {
                                                    'start': {
                                                        'lte': date_start,
                                                    },
                                                    'end': {
                                                        'gte': date_end,
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            'start': {
                                                'gte': date_start,
                                                'lte': date_end,
                                            }
                                        },
                                        {
                                            'end': {
                                                'lte': date_end,
                                                'gte': date_start,
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    )

                    for event in events:
                        output_id = event.output_id
                        input_id = event.input_id

                        if self.outputs[output_id].input_id == input_id:
                            continue # up to date

                        await self.send_routing_update(output_id, input_id)

                await asyncio.sleep(5)
        except:
            logging.exception("Error at loop")

    def __eq__(self, obj):
        return isinstance(obj, Videohub) and obj.id == self.id


def getConfigEntry(lines, index):
    line = lines[index]
    return line[(line.index(":") + 1):].strip()

def getCorrespondingLines(lines, look):
    found = False

    index = -1
    for i in range(len(lines)):
        if lines[i] == look:
            index = i
            break

    if index == -1:
        raise SyntaxError(f"[{self.id}] Entry not found in videohub response: {look}")

    n = []
    for i in range(index + 1, len(lines)):
        line = lines[i]
        if line == "":
            break; # end

        n.append(line)

    return n

def getLines(input):
    lines = input.split("\n")
    # trim those lines
    for i in range(len(lines)):
        lines[i] = lines[i].strip()

    return lines

async def get_next_events():
    #date_start = datetime.now()
    #date_end = date_start + timedelta(minutes=1)

    try:
        events = await prisma.event.find_many()
        return events
    except:
        logging.exception("Error")

def get_videohub(id) -> Videohub:
    for hub in videohubs:
        if hub.id == id:
            return hub

def start() -> None:
    loop = asyncio.get_event_loop()
    for hub in videohubs:
        hub.connect_initial()
        #loop.run_until_complete(hub.socket_connect())

    loop.run_forever()

async def init_controller() -> None:
    print("Controller init...")
    await prisma.connect()

    hubs = await prisma.videohub.find_many()
    for hub in hubs:
        exists = get_videohub(hub.id)

        if exists is None:
            exists = Videohub(hub.id, hub.ip)
            videohubs.append(exists)

    print("Controller init complete.")

async def destroy():
    await prisma.disconnect()
    logging.info("DB disconnected.")

    for hub in videohubs:
        if hub.sock is not None:
            sock.close()

    logging.info("Closed sockets.")
