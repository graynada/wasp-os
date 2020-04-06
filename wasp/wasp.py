# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2020 Daniel Thompson
"""WASP system management (including constants)

.. data:: system = Manager()

   system is the system-wide instance of the Manager class. Applications
   can use this instance to access system services.
"""

import gc
import machine
import watch
import widgets

from apps.clock import ClockApp
from apps.flashlight import FlashlightApp
from apps.launcher import LauncherApp
from apps.testapp import TestApp

class EventType():
    """Enumerated interface actions.

    MicroPython does not implement the enum module so EventType
    is simply a regular object which acts as a namespace.
    """
    DOWN = 1
    UP = 2
    LEFT = 3
    RIGHT = 4
    TOUCH = 5

    HOME = 256

class EventMask():
    """Enumerated event masks.
    """
    TOUCH = 0x0001
    SWIPE_LEFTRIGHT = 0x0002
    SWIPE_UPDOWN = 0x0004
    BUTTON = 0x0008

class PinHandler():
    """Pin (and Signal) event generator.

    TODO: Currently this driver doesn't actually implement any
    debounce but it will!
    """

    def __init__(self, pin):
        """
        :param Pin pin: The pin to generate events from
        """
        self._pin = pin
        self._value = pin.value()

    def get_event(self):
        """Receive a pin change event.

        Check for a pending pin change event and, if an event is pending,
        return it.

        :return: boolean of the pin state if an event is received, None
        otherwise.
        """
        new_value = self._pin.value()
        if self._value == new_value:
            return None

        self._value = new_value
        return new_value

class Manager():
    """WASP system manager

    The manager is responsible for handling top-level UI events and
    dispatching them to the foreground application. It also provides
    services to the application.

    The manager is expected to have a single system-wide instance
    which can be accessed via :py:data:`wasp.system` .
    """

    def __init__(self):
        self.app = None

        self.applications = []
        self.blank_after = 15
        self.charging = True
        self.launcher = LauncherApp()

        self._brightness = 2
        self._button = PinHandler(watch.button)

        # TODO: Eventually these should move to main.py
        self.register(ClockApp(), True)
        self.register(FlashlightApp(), True)
        self.register(TestApp(), True)

    def register(self, app, quick_ring=True):
        """Register an application with the system.

        :param object app: The application to regsister
        """
        self.applications.append(app)

    @property
    def brightness(self):
        """Cached copy of the brightness current written to the hardware."""
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        self._brightness = value
        watch.backlight.set(self._brightness)

    def switch(self, app):
        """Switch to the requested application.
        """
        if self.app:
            if 'background' in dir(self.app):
                self.app.background()
        else:
            # System start up...
            watch.display.poweron()
            watch.display.mute(True)
            watch.backlight.set(self._brightness)
            self.sleep_at = watch.rtc.uptime + 90

        # Clear out any configuration from the old application
        self.event_mask = 0
        self.tick_period_ms = 0
        self.tick_expiry = None

        self.app = app
        watch.display.mute(True)
        watch.drawable.reset()
        app.foreground()
        watch.display.mute(False)

    def navigate(self, direction=None):
        """Navigate to a new application.

        Left/right navigation is used to switch between applications in the
        quick application ring. Applications on the ring are not permitted
        to subscribe to :py:data`EventMask.SWIPE_LEFTRIGHT` events.

        Swipe up is used to bring up the launcher. Clock applications are not
        permitted to subscribe to :py:data`EventMask.SWIPE_UPDOWN` events since
        they should expect to be the default application (and is important that
        we can trigger the launcher from the default application).

        :param int direction: The direction of the navigation
        """
        app_list = self.applications

        if direction == EventType.LEFT:
            if self.app in app_list:
                i = app_list.index(self.app) + 1
                if i >= len(app_list):
                    i = 0
            else:
                i = 0
            self.switch(app_list[i])
        elif direction == EventType.RIGHT:
            if self.app in app_list:
                i = app_list.index(self.app) - 1
                if i < 0:
                    i = len(app_list)-1
            else:
                i = 0
            self.switch(app_list[i])
        elif direction == EventType.UP:
            self.switch(self.launcher)
        elif direction == EventType.DOWN:
            if self.app != app_list[0]:
                self.switch(app_list[0])
            else:
                watch.vibrator.pulse()
        elif direction == EventType.HOME:
            if self.app != app_list[0]:
                self.switch(app_list[0])
            else:
                self.sleep()

    def request_event(self, event_mask):
        """Subscribe to events.

        :param int event_mask: The set of events to subscribe to.
        """
        self.event_mask |= event_mask

    def request_tick(self, period_ms=None):
        """Request (and subscribe to) a periodic tick event.

        Note: With the current simplistic timer implementation sub-second
        tick intervals are not possible.
        """
        self.tick_period_ms = period_ms
        self.tick_expiry = watch.rtc.get_uptime_ms() + period_ms

    def keep_awake(self):
        """Reset the keep awake timer."""
        self.sleep_at = watch.rtc.uptime + self.blank_after

    def sleep(self):
        """Enter the deepest sleep state possible.
        """
        watch.backlight.set(0)
        if 'sleep' not in dir(self.app) or not self.app.sleep():
            self.switch(self.applications[0])
            self.app.sleep()
        watch.display.poweroff()
        self.charging = watch.battery.charging()
        self.sleep_at = None

    def wake(self):
        """Return to a running state.
        """
        watch.display.poweron()
        self.app.wake()
        watch.backlight.set(self._brightness)

        # Discard any pending touch events
        _ = watch.touch.get_event()

        self.keep_awake()

    def _handle_button(self, state):
        """Process a button-press (or unpress) event.
        """
        self.keep_awake()

        if bool(self.event_mask & EventMask.BUTTON):
            # Currently we only support one button
            if not self.app.press(EventType.HOME, state):
                # If app reported None or False then we are done
                return

        if state:
            self.navigate(EventType.HOME)

    def _handle_touch(self, event):
        """Process a touch event.
        """
        self.keep_awake()

        event_mask = self.event_mask
        if event[0] < 5:
            updown = event[0] == 1 or event[0] == 2
            if (bool(event_mask & EventMask.SWIPE_UPDOWN) and updown) or \
               (bool(event_mask & EventMask.SWIPE_LEFTRIGHT) and not updown):
                if self.app.swipe(event):
                    self.navigate(event[0])
            else:
                self.navigate(event[0])
        elif event[0] == 5 and self.event_mask & EventMask.TOUCH:
            self.app.touch(event)

    def _tick(self):
        """Handle the system tick.

        This function may be called frequently and includes short
        circuit logic to quickly exit if we haven't reached a tick
        expiry point.
        """
        rtc = watch.rtc

        if self.sleep_at:
            if rtc.update() and self.tick_expiry:
                now = rtc.get_uptime_ms()

                if self.tick_expiry <= now:
                    ticks = 0
                    while self.tick_expiry <= now:
                        self.tick_expiry += self.tick_period_ms
                        ticks += 1
                    self.app.tick(ticks)

            state = self._button.get_event()
            if None != state:
                self._handle_button(state)

            event = watch.touch.get_event()
            if event:
                self._handle_touch(event)

            if self.sleep_at and watch.rtc.uptime > self.sleep_at:
                self.sleep()

            gc.collect()
        else:
            watch.rtc.update()

            charging = watch.battery.charging()
            if 1 == self._button.get_event() or self.charging != charging:
                self.wake()

    def run(self):
        """Run the system manager synchronously.

        This allows all watch management activities to handle in the
        normal execution context meaning any exceptions and other problems
        can be observed interactively via the console.
        """
        if not self.app:
            self.switch(self.applications[0])

        # Reminder: wasptool uses this string to confirm the device has
        # been set running again.
        print('Watch is running, use Ctrl-C to stop')

        while True:
            self._tick()
            # Currently there is no code to control how fast the system
            # ticks. In other words this code will break if we improve the
            # power management... we are currently relying on no being able
            # to stay in the low-power state for very long.
            machine.deepsleep()

system = Manager()