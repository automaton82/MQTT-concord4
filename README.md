# MQTT-concord4

This is based off the original device-concord4 here:
https://github.com/automaton82/device-concord4

Which is forked from the original here:
https://github.com/csdozier/device-concord4

The original concord4 was made for SmartThings via a web-server. Since SmartThings is now sun-setting classic along with custom DTH UIs, it no longer works.

As such I'm moving to Home Assistant and still want to use this integration. Since MQTT is very easy to use with HA, I've modified the primary server to use MQTT instead of RESTful.

# NOTES

The previous ST version supported 'loud' as an option for arming / disarming, but I didn't implement that as my panel doesn't support it anyways. If desired it could be added back as the payload for the various topics.
