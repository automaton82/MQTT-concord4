# MQTT-concord4

This is based off the original device-concord4 here:
https://github.com/automaton82/device-concord4

Which is forked from the original here:
https://github.com/csdozier/device-concord4

The original concord4 was made for SmartThings via a web-server. Since SmartThings is now sun-setting classic along with custom DTH UIs, it no longer works.

As such I'm moving to Home Assistant and still want to use this integration. Since MQTT is very easy to use with HA, I've modified the primary server to use MQTT instead of RESTful.

## Prerequisites

 - Hardware (Concord 4 or equivalent panel) with a Superbus 2000 automation module attached to it
 - RS232 connection (to the AM panel)
 - Python 2.7
 - Python packages: requests, future, pyserial (pip install), paho-mqtt (pip install)
 - Raspberry Pi (recommended)

## Installation

1. Edit *concordsvr_mqtt.conf* with your favourite editor, such as *nano concordsvr_mqtt.conf*
    * Set *host* to the host of your MQTT, and the *port*
2.  Start the program using **python concordsvr_mqtt.py**
3.  If desired, it can be started on every boot by using *crontab -e* and adding:
    * *@reboot cd /home/pi/ && ./start_concordsvr &*
    
    Where *start_concordsvr* contains:
    * *#!/bin/bash*
    * *cd /home/pi/concordsvr/ && python ./concordsvr_mqtt.py*

## MQTT

The topic listened to is *concord/*. Options include:

*  concord/refresh
*  concord/arm/away
*  concord/arm/stay
*  concord/disarm

## Home Assistant

You can create entities in Home Assistant to represent the various sensors by adding MQTT library to HA, and then editing the *configuration.yaml* file to include something like:

    # MQTT binary sensor
    binary_sensor:
        - platform: mqtt
        name: "Front Door"
        state_topic: "concord/zone/1"
        payload_on: "open"
        payload_off: "closed"
        device_class: opening

This adds the front door sensor as an entity you can put on any dashboard.

## Notes

The previous ST version supported 'loud' as an option for arming / disarming, but I didn't implement that as my panel doesn't support it anyways. If desired it could be added back as the payload for the various topics.
