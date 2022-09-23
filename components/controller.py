import asyncio
from prisma import Prisma
import socket
import time
import sys
from threading import Thread
import logging

INPUT_LABELS = "INPUT LABELS:"
PROTOCOL_PREAMPLE = "PROTOCOL PREAMPLE:"
OUTPUT_START = "OUTPUT LABELS:"
VIDEO_OUTPUT_ROUTING = "VIDEO OUTPUT ROUTING:"
FRIENDLY_NAME = "Friendly name:"

class Input:
    def __init__(self, id, label):
        self.id = int(id)
        self.label = label

class Output:
    def __init__(self, id, label):
        self.id = int(id)
        self.label = label
        self.input_id = None

class Videohub:
    def __init__(self, id, ip):
        self.id = int(id)
        self.ip = ip
        self.name = "undefined"
        self.version = "undefined"
        self.inputs = [] # in case hub does not send initial config
        self.outputs = []
        self.connected = False
        self.sock = None

    async def load_initial(self, text):
        self.info("Loading initial data.")
        lines = getLines(text)

        # ver and name
        self.version = getConfigEntry(lines, 1)
        self.name = getConfigEntry(lines, 6)

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

        self.send_routing_update(self.outputs[0].id, self.inputs[1].id)


    async def connect_callback(self):
        await self.connect()

    async def run_loop(self, sock):
        self.info("Waiting for data...")
        data = sock.recv(1024).decode('utf-8')
        self.info(f"Received: {data!r}")

        if data.startswith(PROTOCOL_PREAMPLE): # initial
            await self.load_initial(data)
        elif data.startswith(VIDEO_OUTPUT_ROUTING): # update routing
            lines = getCorrespondingLines(getLines(data), VIDEO_OUTPUT_ROUTING)
            i = 0

            for i in range(len(lines)):
                line = lines[i]
                data = line.split(" ")
                output_id = int(data[0])
                if output_id >= len(self.outputs):
                    self.info(f"Output not loaded. Id: {output_id}")
                    continue

                input_id = int(data[1])
                if input_id >= len(self.inputs):
                    self.info(f"Input not loaded: Id: {input_id}")
                    continue

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

                self.info(f"Routing updated: {self.id}: ({output_id}, {input_id})")

    def socket_connect(self):
        if self.connected: # prevent error
            raise Exception(f"[{self.id}] Tried to connect, but already is.")
            return

        if self.sock is not None:
            try:
                self.sock.close() # make sure to close prev one and release memory
            except socket.error:
                logging.exception(f"[{self.id}] Failed to close old socket.")
                return

        c = 1
        self.sock = socket.socket() # create new one
        while not self.connected:
            try:
                self.establish_connection(self.sock, c)
                self.sock.send(b'Hello!')
                self.connected = True
                return self.sock
            except socket.error:
                logging.exception(f"[{self.id}] Couldn't connect to socket.")
                time.sleep(2)
                c += 1

    def establish_connection(self, sock, c):
        self.info(f"Attempting connection (#{c}).")
        sock.connect((self.ip, 9990))
        self.info("Connected to socket.")

    def send_routing_update(self, output, input):
        send = f"{VIDEO_OUTPUT_ROUTING}\n{output} {input}\n"
        self.info(f"Sending routing update: {output}:{input}")

        sock = socket.socket()
        try:
            self.establish_connection(sock, 1)
            sock.recv(1024).decode('utf-8') # skip preamble

            sock.send(send.encode()) # send routing update
            self.info("Routing update sent.")
        except socket.error:
            logging.exception(f"[{self.id}] Couldn't send routing update to videohub.")
        finally:
            sock.close()


    async def connect(self):
        try:
            sock = self.socket_connect() # initial

            while True:
                try:
                    await self.run_loop(sock)
                except socket.error:
                    logging.exception(f"[{self.id}] Lost connection. Attempting reconnect.")
                    self.connected = False
                    sock = self.socket_connect()

        except Exception:
            logging.exception(f"[{self.id}] Failed to connect.")


    def info(self, msg):
        m = f"[{self.id}] {msg}"
        logging.info(m)
        print(m)

    async def save(self):
        self.info("Saving.")

        try:
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
        except:
            logging.exception(f"[{self.id}] Failed to save inputs or outputs.")

        self.info("Saved.")

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

videohubs = []
def get_videohub(id) -> Videohub:
    for hub in videohubs:
        if hub.id == id:
            return hub


def start() -> None:
    loop = asyncio.get_event_loop()
    tasks = []
    for hub in videohubs:
        tasks.append(loop.create_task(hub.connect()))

    loop.run_until_complete(asyncio.wait(tasks))

videohubs = []
prisma = Prisma()
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
