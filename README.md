# MQTT-concord4

This is based off the original device-concord4 here:
https://github.com/automaton82/device-concord4

Which is forked from the original here:
https://github.com/csdozier/device-concord4

The original concord4 was made for SmartThings via a web-server. Since SmartThings is now sun-setting classic along with custom DTH UIs, it no longer works.

As such I'm moving to Home Assistant and still want to use this integration. Since MQTT is very easy to use with HA, I've modified the primary server to use MQTT instead of RESTful.
