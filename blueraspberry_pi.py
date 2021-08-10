#!/usr/bin/env python
# Modified from code at https://github.com/tknapstad/BluePloverPi/
# Code fixed from http://www.linuxuser.co.uk/tutorials/emulate-a-bluetooth-keyboard-with-the-raspberry-pi
# Updated to work with Bluez 5


import os
import sys
import bluetooth
from bluetooth import *
import dbus
import time
import evdev
from evdev import *

import uuid

BT_ADAPTER_PATH = "/org/bluez/hci0"
BT_UUID = str(uuid.uuid4())


class Bluetooth:
    P_CTRL = 17
    P_INTR = 19

    HOST = 0
    PORT = 1

    def __init__(self):
        self.scontrol = BluetoothSocket(L2CAP)
        self.sinterrupt = BluetoothSocket(L2CAP)
        self.scontrol.bind(("", Bluetooth.P_CTRL))
        self.sinterrupt.bind(("", Bluetooth.P_INTR))
        self.bus = dbus.SystemBus()

        adapter = dbus.Interface(
            self.bus.get_object("org.bluez", BT_ADAPTER_PATH),
            "org.freedesktop.DBus.Properties",
        )

        # The Name and Class of a device should be statically configured
        # according to the Bluez dbus interface docs. These are set
        # in /etc/bluetooth/main.conf

        adapter.Set("org.bluez.Adapter1", "Alias", "Bluetooth N64 Controller")
        adapter.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(1))
        adapter.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))
        adapter.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(1))
        adapter.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
        adapter.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(1))

        # Register the SDP defined in the XML file
        self.manager = dbus.Interface(
            self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1"
        )

        with open(sys.path[0] + "/sdp_record.xml", "r") as fh:
            self.service_record = fh.read()

    def listen(self):
        profile = {
            "ServiceRecord": self.service_record,
        }

        # Register our device profile
        self.manager.RegisterProfile(BT_ADAPTER_PATH, BT_UUID, profile)
        print("Service record added")

        self.scontrol.listen(1)  # Limit of 1 connection
        self.sinterrupt.listen(1)
        print("Waiting for a connection")
        self.ccontrol, self.cinfo = self.scontrol.accept()
        print(
            "Got a connection on the control channel from " + self.cinfo[Bluetooth.HOST]
        )
        self.cinterrupt, self.cinfo = self.sinterrupt.accept()
        print(
            "Got a connection on the interrupt channel fro "
            + self.cinfo[Bluetooth.HOST]
        )

    def send_input(self, ir):
        """Convert specified bytes and lists of bits into appropriate format and send."""
        #  Convert the hex array to a string
        hex_str = ""
        for element in ir:
            if type(element) is list:
                # This is our bit array - convrt it to a single byte represented
                # as a char
                bin_str = ""
                for bit in element:
                    bin_str += str(bit)
                hex_str += chr(int(bin_str, 2))
            else:
                # This is a hex value - we can convert it straight to a char
                hex_str += chr(element)
        # Send an input report
        self.cinterrupt.send(hex_str)


class Joystick:
    # Only consider devices with at least 8 buttons as joysticks
    JOYSTICK_BTN_THRESHOLD = 8

    # Event types
    # TODO use well-known constants, like this from evdev ecodes where possible
    # Buttons like Start, Z, A, B
    TYPE_BTN = 1
    # Joysticks like the joystick or Dpad
    TYPE_JOY = 3

    # Button IDs from the USB device
    BTN_YELLOW_UP = 288
    BTN_YELLOW_RIGHT = 289
    BTN_YELLOW_DOWN = 290
    BTN_YELLOW_LEFT = 291
    BTN_L_BUMP = 292
    BTN_R_BUMP = 293
    BTN_A = 294
    BTN_Z = 295
    BTN_B = 296
    BTN_START = 297

    # Joystick axis IDs from the USB device
    JOY_THUMB_HORIZ = 0
    JOY_THUMB_VERT = 1
    JOY_THUMB_HORIZ_ALT = 2
    JOY_DPAD_HORIZ = 16
    JOY_DPAD_VERT = 17

    # Make signal less noisy, since the joystick reports lots of bogus changes
    ANALOG_THRESH_LO = 127
    ANALOG_THRESH_HI = 135

    # Not sure why these show up, but seems like we can ignore them
    # 0 is some empty event and 4 appears somewhat redundant with key down
    IGNORED_EVENT_TYPES = {0, 4}

    def __init__(self):
        # Button-pressed booleans
        self.buttons = {
            Joystick.BTN_YELLOW_UP: 0,
            Joystick.BTN_YELLOW_RIGHT: 0,
            Joystick.BTN_YELLOW_DOWN: 0,
            Joystick.BTN_YELLOW_LEFT: 0,
            Joystick.BTN_L_BUMP: 0,
            Joystick.BTN_R_BUMP: 0,
            Joystick.BTN_A: 0,
            Joystick.BTN_Z: 0,
            Joystick.BTN_B: 0,
            Joystick.BTN_START: 0,
        }

        # Joystick/directional axes values
        self.axes = {
            Joystick.JOY_THUMB_HORIZ: 0x80,
            Joystick.JOY_THUMB_VERT: 0x80,
            Joystick.JOY_THUMB_HORIZ_ALT: 0x80,
            Joystick.JOY_DPAD_HORIZ: 0,  # From -1 to 1 (right to left, respectively)
            Joystick.JOY_DPAD_VERT: 0,  # From -1 to 1 (down to up, respectively)
        }
        self.wait_for_device()

    def wait_for_device(self):
        """Loop until we get a device"""
        self.dev = None
        while not self.dev:
            devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
            for device in devices:
                if self.btn_count(device) >= Joystick.JOYSTICK_BTN_THRESHOLD:
                    self.dev = device
                    break
            if self.dev:
                break
            print(
                "Joystick not found in {count} devices, waiting 3 seconds and retrying ({devices})".format(
                    count=len(devices), devices=devices
                )
            )
            time.sleep(3)
        print("Found a joystick ({dev})".format(dev=self.dev))

    def btn_count(self, device):
        """Count how many buttons exist for the specified device. Used by heuristic to identify joystick vs keyboard/mouse/etc."""
        cap = device.capabilities(verbose=True)
        if ("EV_KEY", ecodes.EV_KEY) not in cap:
            return 0
        # for v in cap.get(('EV_KEY', ecodes.EV_KEY)):
        #  print(v)
        btns = ["BTN" in v[0] for v in cap.get(("EV_KEY", ecodes.EV_KEY))]
        return btns.count(True)

    def near_origin(self, value):
        """Returns a boolean indicating the specified axis value is near the origin. I.e. if it should be rounded to 0x80."""
        return value > Joystick.ANALOG_THRESH_LO and value < Joystick.ANALOG_THRESH_HI

    def apply_event(self, event):
        """Apply specified event to current state. Return boolean indicating if state effectively changed.

        Some things like minor joystick drift can be ignored here."""
        c = event.code
        t = event.type
        input_value = event.value

        changed = True

        if t == Joystick.TYPE_BTN:
            assert c in self.buttons
            self.buttons[c] = input_value
        elif t == Joystick.TYPE_JOY:
            old_val = self.axes[c]
            if self.near_origin(input_value) and self.near_origin(old_val):
                changed = False

            self.axes[c] = input_value
        else:
            # TODO warn about unrecognized stuff that we might care about
            pass
        if changed:
            print("applied event: {event}\n".format(event=event))
        else:
            # Could log debug here
            pass
        return changed

    def event_loop(self, bt):
        for event in self.dev.read_loop():
            if (
                event
                and event.type not in Joystick.IGNORED_EVENT_TYPES
                and self.apply_event(event)
            ):
                bt.send_input(self.build_report())

    def get_dpad_encoding(self):
        """Convert dpad directions into the rotational-encoding array."""
        up = self.axes[Joystick.JOY_DPAD_VERT] == -1
        down = self.axes[Joystick.JOY_DPAD_VERT] == 1
        right = self.axes[Joystick.JOY_DPAD_HORIZ] == 1
        left = self.axes[Joystick.JOY_DPAD_HORIZ] == -1

        if up and right:
            return [0, 0, 0, 1]
        if down and right:
            return [0, 0, 1, 1]
        if down and left:
            return [0, 1, 0, 1]
        if up and left:
            return [0, 1, 1, 1]
        if up:
            return [0, 0, 0, 0]
        if right:
            return [0, 0, 1, 0]
        if down:
            return [0, 1, 0, 0]
        if left:
            return [0, 1, 1, 0]
        return [1, 1, 1, 1]

    def build_report(self):
        """Build an input report for the current state."""
        return [
            0xA1,  # This is an input report
            0x01,  # Report Id
            self.axes[Joystick.JOY_THUMB_HORIZ],
            self.axes[Joystick.JOY_THUMB_VERT],
            self.axes[Joystick.JOY_THUMB_HORIZ_ALT],
            0x80,  # Not sure what these are
            0x80,
            # TODO verify the ordering is right
            [
                self.buttons[Joystick.BTN_YELLOW_LEFT],
                self.buttons[Joystick.BTN_YELLOW_DOWN],
                self.buttons[Joystick.BTN_YELLOW_RIGHT],
                self.buttons[Joystick.BTN_YELLOW_UP],
            ]
            + self.get_dpad_encoding(),
            [
                0,
                0,
                self.buttons[Joystick.BTN_START],
                self.buttons[Joystick.BTN_B],
                self.buttons[Joystick.BTN_Z],
                self.buttons[Joystick.BTN_A],
                self.buttons[Joystick.BTN_R_BUMP],
                self.buttons[Joystick.BTN_L_BUMP],
            ],
            # Not sure what these are for...
            [
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
            ],
        ]


if __name__ == "__main__":
    if not os.geteuid() == 0:
        sys.exit("Only root can run this script")
    bt = Bluetooth()
    bt.listen()
    dev = Joystick()
    dev.event_loop(bt)
