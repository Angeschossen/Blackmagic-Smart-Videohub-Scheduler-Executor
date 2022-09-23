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

    async def load_initial(self, text):
        print("Loading initial data.")
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
        print("Initial data loaded.")


    async def connect_callback(self):
        await self.connect()

    async def connect(self):
        print("Attempting connection.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.ip, 9990))

            while True:
                print("Waiting for data.")
                data = s.recv(1024).decode('utf-8')
                print(f"Received: {data!r}")

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
                            logging.info(f"Output not loaded. Id: {output_id}")
                            continue

                        input_id = int(data[1])
                        if input_id >= len(self.inputs):
                            logging.info(f"Input not loaded: Id: {input_id}")
                            continue

                        self.outputs[output_id].input_id = input_id
                        logging.info(f"Routing: {self.id}: ({output_id}, {input_id})")


    async def test(self):
        print("Test")

    async def save(self):
        print("Saving videohub.")
        try:
            v = False
            await self.test()
            for input in self.inputs:
                id = input.id
                try:
                    print(f"Save: {id}")
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
                except:
                    print(f"Error at {id} {input.label}")
                    print(sys.exc_info())

                print("Saved input")

            for output in self.outputs:
                print(self.id)
                print(output.id)
                print(output.input_id)
                print(output.label)

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

                print("Saved output")
        except:
            print(sys.exc_info())

        print("Videohub saved.")

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
        raise SyntaxError("Entry not found in videohub response: " + look)

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


prisma = Prisma()

async def main() -> None:
    print("Starting")
    await prisma.connect()
    print("Connected to db.")

    hubs = await prisma.videohub.find_many()
    for hub in hubs:
        exists = get_videohub(hub.id)

        if exists is None:
            exists = Videohub(hub.id, hub.ip)
            videohubs.append(exists)
            #thread = Thread(target = asyncio.run, args = (exists.connect_callback()))
            #thread.start()
            await exists.connect()
            #asyncio.get_event_loop().create_task(exists.connect())

            #exists.thread = thread


if __name__ == '__main__':
    # set the logging config
    logging.basicConfig(handlers=[logging.FileHandler('app.log', 'a+', 'utf-8')], level=logging.INFO, format='%(asctime)s: %(message)s')

    asyncio.run(main())
    for hub in videohubs:
        hub.thread.join()

    time.sleep(50000)
    print("STOOOP")
    d = prisma.disconnect()
