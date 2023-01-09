import asyncio
from prisma import Prisma
import socket
import time
import sys
from threading import Thread
import logging
import atexit
###

if __name__ == '__main__':
    print("Initializing...")
    from components import controller

    # set the logging config
    logging.basicConfig(handlers=[logging.FileHandler('app.log', 'a+', 'utf-8')], level=logging.INFO, format='%(asctime)s: %(message)s')
    loop = asyncio.get_event_loop()

    def exit_handler():
        loop.run_until_complete(controller.destroy())
        loop.close()

    loop.run_until_complete(controller.init_controller())
    controller.start()

    # register exit routine
    atexit.register(exit_handler)
    print("End.")
