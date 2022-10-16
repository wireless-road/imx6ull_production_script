import subprocess
from multiprocessing import Process, Queue
from time import sleep
import sys, getopt
import socket
import serial
import threading

CFG_OPENOCD_SERVER_EXE = "openocd.exe"
CFG_TELNET_ADDRESS = "127.0.0.1"
CFG_TELNET_PORT = 3000
CFG_COM_PORT = ""#"COM5"
CFG_COM_PORT_BAUDRATE = 115200
CFG_U_BOOT_IMAGE = "u-boot.imx"
CFG_KERNEL_IMAGE = ""#"openwrt-imx6ull-cortexa7-video_stream_ethernet-squashfs.mtd-factory.bin"

OPENOCD_LOG_MSG__JTAG_DEVICE_DETECTED = "Info : imx6ull.cpu.0: hardware has 6 breakpoints, 4 watchpoints"
OPENOCD_LOG_MSG__JTAG_DEVICE_NOT_DETECTED = "Error: No J-Link device found."
OPENOCD_LOG_MSG__TELNET_CONNECTION_ACCEPTED = "Info : accepting 'telnet' connection on tcp/3000"
OPENOCD_LOG_MSG__JTAG_CPU_RESET_SUCESSFUL = "core state: ARM"
OPENOCD_LOG_MSG__JTAG_U_BOOT_IMAGE_DOWNLOADED = "downloaded "

COM_LOG_MSG__U_BOOT_STARTED = "CPU"
COM_LOG_MSG__U_BOOT_CMD_ACCESS = "Hit any key to stop autoboot"
COM_LOG_MSG__CONSOLE_ACCESSED = "=>"
COM_LOG_MSG__FLASH_DETECTED = "SF: Detected"
COM_LOG_MSG__FLASH_WRITTEN = "bytes written,"
COM_LOG_MSG__FUSE_BITS_BURNED = "Programming bank"


COM_CMD_CHECK_FLASH = "sf probe "
COM_CMD_RESET_DEVICE = "reset "

COM_CMD_WRITE_FIRMWARE_TO_FLASH = "sf update 0x82000000 0x0 0x1000000 "
COM_CMD_BURN_FUSE_BITS_1 = "fuse prog -y 0 5 0x0a000030 "
COM_CMD_BURN_FUSE_BITS_2 = "fuse prog -y 0 6 0x00000010 "
# COM_CMD_WRITE_FIRMWARE_TO_FLASH = "sf update 0x82000000 0x1000 0x8000 "
# COM_CMD_BURN_FUSE_BITS_1 = "ls"
# COM_CMD_BURN_FUSE_BITS_2 = "ls"


TELNET_CMD_RESTART_CPU = "reset init; arm core_state arm; halt;"
TELNET_CMD_LOAD_U_BOOT_IMAGE = "load_image " + CFG_U_BOOT_IMAGE + " 0x877ff400"
TELNET_CMD_LOAD_KERNEL_IMAGE = "load_image " + CFG_KERNEL_IMAGE + " 0x82000000"
TELNET_CMD_START_U_BOOT = "resume 0x87800000"

STATE_POWER_ON = "POWER_ON"
STATE_JTAG_DEVICE_DETECTED = "JTAG DEVICE DETECTED"
STATE_JTAG_DEVICE_NOT_DETECTED = "JTAG DEVICE NOT DETECTED"
STATE_TELNET_CLIENT_CONNECTED = "TELNET CLIENT CONNECTED"
STATE_CPU_RESET_COMPLETED = "CPU RESET COMPLETED"
STATE_IMAGE_LOADED = "IMAGE_LOADED"
STATE_U_BOOT_STARTED = "UBOOT_STARTED"
STATE_U_BOOT_CONSOLE_ACCESSED = "UBOOT_CONSOLE_ACCESS_AVAILABLE"
STATE_FLASH_IC_DETECTED = "FLASH_IC_DETECTED"
STATE_FIRMWARE_FLASHED_TO_IC = "FIRMWARE_FLASHED"
STATE_FUSE_BITS_BURNED = "FUSE_BITS_BURNED"


def output_reader(proc):
    for line in iter(proc.stdout.readline, b''):
        print("OPENOCD.exe: ", line)


def run_openocd_server(device_current_state):
    process_openocd = subprocess.Popen(CFG_OPENOCD_SERVER_EXE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print("proc started")
    for line in iter(process_openocd.stderr.readline, ''):
        line_decoded = line.decode('utf-8').replace("\r", "").replace("\n", "")
        print("OPENOCD:", line_decoded)
        if OPENOCD_LOG_MSG__JTAG_DEVICE_DETECTED in line_decoded:
            device_current_state.put(STATE_JTAG_DEVICE_DETECTED)
        elif OPENOCD_LOG_MSG__JTAG_DEVICE_NOT_DETECTED in line_decoded:
            device_current_state.put(STATE_JTAG_DEVICE_NOT_DETECTED)
        elif OPENOCD_LOG_MSG__TELNET_CONNECTION_ACCEPTED in line_decoded:
            device_current_state.put(STATE_TELNET_CLIENT_CONNECTED)
        elif OPENOCD_LOG_MSG__JTAG_CPU_RESET_SUCESSFUL in line_decoded:
            device_current_state.put(STATE_CPU_RESET_COMPLETED)
        elif OPENOCD_LOG_MSG__JTAG_U_BOOT_IMAGE_DOWNLOADED in line_decoded:
            device_current_state.put(STATE_IMAGE_LOADED)


def run_telnet_client_listener(telnet_socket):
    try:
        telnet_socket.connect((CFG_TELNET_ADDRESS, CFG_TELNET_PORT))
    except:
        print("ERROR: TELNET. Unable to connect")
        sys.exit()

    while True:
            data = telnet_socket.recv(4096)
            if not data:
                sys.exit()

def run_telnet_client(telnet_socket_write_queue):
    telnet_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    thread = threading.Thread(target=run_telnet_client_listener, args=(telnet_socket,))
    thread.start()

    while True:
        if not telnet_socket_write_queue.empty():
            command = telnet_socket_write_queue.get()
            print("command: ", command)
            telnet_socket.send(command)
        sleep(1)



def run_com_port_listener(device_current_state, serialPort):
    print("run com port")
    while True:
        if(serialPort.in_waiting > 0):
            data = serialPort.readline()
            data_decoded = data.decode('Ascii')
            print("COM:", data_decoded)
            if COM_LOG_MSG__U_BOOT_STARTED in data_decoded:
                device_current_state.put(STATE_U_BOOT_STARTED)
                serialPort.write(" \r\n".encode())
                sleep(1)
                serialPort.write(" \r\n".encode())
            elif COM_LOG_MSG__CONSOLE_ACCESSED in data_decoded:
                device_current_state.put(STATE_U_BOOT_CONSOLE_ACCESSED)
            elif COM_LOG_MSG__FLASH_DETECTED in data_decoded:
                device_current_state.put(STATE_FLASH_IC_DETECTED)
            elif COM_LOG_MSG__FLASH_WRITTEN in data_decoded:
                device_current_state.put(STATE_FIRMWARE_FLASHED_TO_IC)
            elif COM_LOG_MSG__FUSE_BITS_BURNED in data_decoded:
                device_current_state.put(STATE_FUSE_BITS_BURNED)


def run_com_port_handler(device_current_state, commands_to_send, CFG_COM_PORT):
    global CFG_COM_PORT_BAUDRATE
    serialPort = serial.Serial(port=CFG_COM_PORT, baudrate=CFG_COM_PORT_BAUDRATE, bytesize=8)
    thread = threading.Thread(target=run_com_port_listener, args=(device_current_state, serialPort,))
    thread.start()

    while True:
        if not commands_to_send.empty():
            command = commands_to_send.get()
            serialPort.write(command.encode())
        sleep(1)


def check_state(device_state, desired_state, duration_sec=10):
    for i in range(duration_sec):
        if not device_state.empty():
            current_state = device_state.get()
            if current_state == desired_state:
                return 0
        sleep(1)
    print("ERROR: desired state is ", desired_state, " but actual is not")
    sys.exit()


def parse_args(argv):
    global CFG_COM_PORT, CFG_KERNEL_IMAGE, TELNET_CMD_LOAD_KERNEL_IMAGE
    try:
        opts, args = getopt.getopt(argv, "c:f:h", ["com=","file="])
    except getopt.GetoptError as err:
        print('production.py -c <com_port> -f <image_filename>')
        sys.exit(2)
    if len(opts) < 2:
        print('Production.py -c <com_port> -f <image_filename>')
        sys.exit(2)

    for opt, arg in opts:
        if opt == '-h':
            print('production.py -c <com_port> -f <image_filename>')
        elif opt in ("-c", "--com_port"):
            CFG_COM_PORT = arg
            print("COM port ", CFG_COM_PORT)
        elif opt in ("-f", "--file"):
            CFG_KERNEL_IMAGE = arg
            TELNET_CMD_LOAD_KERNEL_IMAGE = "load_image " + CFG_KERNEL_IMAGE + " 0x82000000"
            print("firmware file ", CFG_KERNEL_IMAGE)
        else:
            print("unknown arg ", opt, ". Use: production.py -c <com_port> -f <image_filename>")
            sys.exit(2)

    if CFG_COM_PORT == "" or CFG_KERNEL_IMAGE == "":
        sys.exit(2)


if __name__ == '__main__':

    parse_args(sys.argv[1:])

    # 1. Init OpenOCD server
    device_state = Queue()
    p_openocd = Process(target=run_openocd_server, args=(device_state,))
    p_openocd.start()
    check_state(device_state, STATE_JTAG_DEVICE_DETECTED)

    # 2. Init. Connect to OpenOCD server via Telnet
    telnet_socket_write_queue = Queue()
    p_telnet = Process(target=run_telnet_client, args=(telnet_socket_write_queue,))
    p_telnet.start()
    check_state(device_state, STATE_TELNET_CLIENT_CONNECTED)

    # 3. Init. Open COM port.
    device_state_com = Queue()
    com_port_write_queue = Queue()
    p_com_port = Process(target=run_com_port_handler, args=(device_state_com,com_port_write_queue,CFG_COM_PORT,))
    p_com_port.start()

    # 4. Reset CPU
    telnet_socket_write_queue.put((TELNET_CMD_RESTART_CPU + "\r\n").encode())
    check_state(device_state, STATE_CPU_RESET_COMPLETED)

    # 5. Load u-boot image
    telnet_socket_write_queue.put((TELNET_CMD_LOAD_U_BOOT_IMAGE + "\r\n").encode())
    check_state(device_state, STATE_IMAGE_LOADED, 30)

    # 6. Load kernel image
    telnet_socket_write_queue.put((TELNET_CMD_LOAD_KERNEL_IMAGE + "\r\n").encode())
    check_state(device_state, STATE_IMAGE_LOADED, 1000)

    # 7. Start U-boot execution
    telnet_socket_write_queue.put((TELNET_CMD_START_U_BOOT + "\r\n").encode())
    check_state(device_state_com, STATE_U_BOOT_CONSOLE_ACCESSED)

    # 8. Check flash IC available
    com_port_write_queue.put(COM_CMD_CHECK_FLASH + "\r")
    check_state(device_state_com, STATE_FLASH_IC_DETECTED)

    # 9. Flash firmware to IC
    com_port_write_queue.put(COM_CMD_WRITE_FIRMWARE_TO_FLASH + "\r")
    check_state(device_state_com, STATE_FIRMWARE_FLASHED_TO_IC, 700)

    # 10. Burn fuse bit (ECSPI3 programming 0x450 = 0x0a000030)
    com_port_write_queue.put(COM_CMD_BURN_FUSE_BITS_1 + "\r")
    check_state(device_state_com, STATE_FUSE_BITS_BURNED)

    # 11. Burn fuse bit (BT_FUSE_SEL programming 0x460 = 0x00000010)
    com_port_write_queue.put(COM_CMD_BURN_FUSE_BITS_2 + "\r")
    check_state(device_state_com, STATE_FUSE_BITS_BURNED)

    # 12. Reset device
    com_port_write_queue.put(COM_CMD_RESET_DEVICE + "\r")
