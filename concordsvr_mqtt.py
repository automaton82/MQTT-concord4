"""
Concord 4 Server for MQTT

Automaton82
8/12/2020

Taken from fork:
https://github.com/automaton82/device-concord4

---------------------------------------

Modified from original:

https://github.com/csdozier/device-concord4
Scott Dozier
4/1/2016


Developed from py-concord Copyright (c) 2013, Douglas S. J. De Couto, decouto@alum.mit.edu


"""

import os
import sys
import time
from threading import Thread
from collections import deque
import datetime
import traceback
import string
import base64
import logging,logging.handlers
import ConfigParser
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
from concord import concord, concord_commands, concord_alarm_codes
from concord.concord_commands import STAR, HASH, TRIPPED, FAULTED, ALARM, TROUBLE, BYPASSED

log = logging.getLogger('root')
version = 3.0

def dict_merge(a, b):
    c = a.copy()
    c.update(b)
    return c

def start_logger():
    FORMAT = "%(asctime)-15s [%(filename)s:%(funcName)1s()] - %(levelname)s - %(message)s"
    logging.basicConfig(format=FORMAT)
    if 'DEBUG' in config.LOGLEVEL.upper():
        log.setLevel(logging.DEBUG)
    elif 'INFO' in config.LOGLEVEL.upper():
        log.setLevel(logging.INFO)
    elif 'ERR' in config.LOGLEVEL.upper():
        log.setLevel(logging.ERROR)
    else :
        log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler('concordsvr_mqtt.log',
                                           maxBytes=2000000,
                                           backupCount=2,
                                           )
    formatter = logging.Formatter(FORMAT)
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.info('Logging started [LEVEL: '+str(config.LOGLEVEL.upper())+']'+'...')

def logger(message, level = 'info'):
    if 'info' in level:
        log.info(message)
    elif 'error' in level:
        log.error(message)
    elif 'debug' in level:
        log.debug(message)
    elif 'critical' in level:
        log.critical(message)
    elif 'warn' in level:
        log.warn(message)

#
# Send e-mail over GMAIL
#
def send_email(user, pwd, recipient, subject, body):
    import smtplib

    gmail_user = user
    gmail_pwd = pwd
    FROM = user
    TO = recipient if type(recipient) is list else [recipient]
    SUBJECT = subject
    TEXT = body

    # Prepare actual message
    message = """From: %s\nTo: %s\nSubject: %s\n\n%s
    """ % (FROM, ", ".join(TO), SUBJECT, TEXT)
    try:
        server_ssl = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server_ssl.ehlo()
        server_ssl.login(gmail_user, gmail_pwd)
        server_ssl.sendmail(FROM, TO, message)
        server_ssl.close()
        log.info("E-mail notification sent")
    except Exception, ex:
        log.error("E-mail notification failed to send: %s" % str(ex))

#
# Send an MQTT update. This has to be on the main thread due to some sort of bug in the MQTT library
#
def send_mqtt_update(topic, value):
    topic = 'concord/'+topic
    logger('TX -> '+str(config.HOST)+':'+str(config.PORT)+' - '+topic+' - '+value)

    try:
        publish.single(topic, value, hostname=config.HOST, port=config.PORT, auth = {'username':config.MQTTUSER.decode('base64'),'password':config.MQTTPASSWORD.decode('base64')})
    except:
        logger("MQTT failed to publish message...")

def zonekey(zoneDev):
    """ Return internal key for supplied Indigo zone device. """
    #assert zoneDev.deviceTypeId == 'zone'
    return (int(zoneDev.pluginProps['partitionNumber']),
            int(zoneDev.pluginProps['zoneNumber']))
    
def partkey(partDev):
    """ Return internal key for supplied Indigo partition or touchpad device. """
    #assert partDev.deviceTypeId in ('partition', 'touchpad')
    return int(partDev.address)

def any_if_blank(s):
    if s == '': return 'any'
    else: return s

def isZoneErrState(state_list):
    for err_state in [ ALARM, FAULTED, TROUBLE, BYPASSED ]:
        if err_state in state_list:
            return True
    return False

def zoneStateChangedExceptTripped(old, new):
    old = list(sorted(old)).remove(TRIPPED)
    new = list(sorted(new)).remove(TRIPPED)
    return old != new
    

#
# Touchpad display when no data available
#
NO_DATA = '<NO DATA>'

#
# Keypad sequences for various actions
#
KEYPRESS_SILENT = [ 0x05 ]
KEYPRESS_ARM_STAY = [ 0x28 ]
KEYPRESS_ARM_AWAY = [ 0x27 ]
KEYPRESS_ARM_STAY_LOUD = [ 0x02 ]
KEYPRESS_ARM_AWAY_LOUD = [ 0x03 ]
KEYPRESS_DISARM = [ 0x20 ]
KEYPRESS_BYPASS = [ 0xb ] # '#'
KEYPRESS_TOGGLE_CHIME = [ 7, 1 ]

KEYPRESS_EXIT_PROGRAM = [ STAR, 0, 0, HASH ]


#
# XML configuration filters
# 
PART_FILTER = [(str(p), str(p)) for p in range(1, concord.CONCORD_MAX_ZONE+1)]
PART_FILTER_TRIGGER = [('any', 'Any')] + PART_FILTER

PART_STATE_FILTER = [ 
    ('unknown', 'Unknown'),
    ('ready', 'Ready'), # aka 'off'
    ('unready', 'Not Ready'), # Not actually a Concord state 
    ('zone_test', 'Phone Test'),
    ('phone_test', 'Phone Test'),
    ('sensor_test', 'Sensor Test'),
    ('stay', 'Armed Stay'),
    ('away', 'Armed Away'),
    ('night', 'Armed Night'),
    ('silent', 'Armed Silent'),
    ]
PART_STATE_FILTER_TRIGGER = [('any', 'Any')] + PART_STATE_FILTER

# Different messages (i.e. PART_DATA and ARM_LEVEL) may
# provide different sets of partitiion arming states; this dict
# unifies them and translates them to the states our Partitiion device
# supports.
PART_ARM_STATE_MAP = {
    # Original arming code -> Partition device state
    -1: 'unknown', # Internal to plugin
    0: 'zone_test', # 'Zone Test', ARM_LEVEL only
    1: 'ready', # 'Off',
    2: 'stay', # 'Home/Perimeter',
    3: 'away', # 'Away/Full',
    4: 'night', # 'Night', ARM_LEVEL only
    5: 'silent', # 'Silent', ARM_LEVEL only
    8: 'phone_test', # 'Phone Test', PART_DATA only
    9: 'sensor_test', # 'Sensor Test', PART_DATA only
}


# Custom dictionary to give friendly names to zones for display
FRIENDLY_ZONE_NAME_MAP = {
    1: "Front Door",
    2: "Back Door",
    3: "Garage Interior Door",
    4: "Main Floor Glass Break",
    5: "Basement Sliding Glass Door",
    6: "Basement Windows",
    7: "Basement Glass Break",
    8: "Smoke / CO2 Alarm",
    9: "Key Fob 1",
    10: "Key Fob 2"
}

class Concord4ServerConfig():
    def __init__(self, configfile):

        self._config = ConfigParser.ConfigParser()
        self._config.read(configfile)

        self.SERIALPORT = self.read_config_var('main', 'serialport', '', 'str')
        self.LOGLEVEL = self.read_config_var('main', 'loglevel', '', 'str')
        self.HOST = self.read_config_var('main', 'host', '', 'str')
        self.PORT = self.read_config_var('main', 'port', 1883, 'int')
        self.EMAILSENDER = self.read_config_var('main', 'emailsender', '', 'str')
        self.EMAILPASSWORD = self.read_config_var('main', 'emailpassword', '', 'str')
        self.EMAILRECIPIENT = self.read_config_var('main', 'emailrecipient', '', 'str')
        self.MQTTUSER = self.read_config_var('main', 'mqttuser', '', 'str')
        self.MQTTPASSWORD = self.read_config_var('main', 'mqttpassword', '', 'str')

    def defaulting(self, section, variable, default, quiet = False):
        if quiet == False:
            print('Config option '+ str(variable) + ' not set in ['+str(section)+'] defaulting to: \''+str(default)+'\'')

    def read_config_var(self, section, variable, default, type = 'str', quiet = False):
        try:
            if type == 'str':
                return self._config.get(section,variable)
            elif type == 'bool':
                return self._config.getboolean(section,variable)
            elif type == 'int':
                return int(self._config.get(section,variable))
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
            self.defaulting(section, variable, default, quiet)
            return default
    def read_config_sec(self, section):
        try:
            return self._config._sections[section]
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
            return {}


class ConcordSvr(object):

    def __init__(self):
        self.panel = None
        self.panelDev = None
        self.panelInitialQueryDone = False
        self.StopThread = False
        self.armed = None
        self.serialPortUrl = config.SERIALPORT

        # Zones are keyed by (partitition number, zone number)
        self.zones = { } # zone key -> dict of zone info, i.e. output of cmd_zone_data
        self.zoneDevs = { } # zone key -> active Indigo zone device
        self.zoneKeysById = { } # zone device ID -> zone key

        # Partitions are keyed by partition number
        self.parts = { } # partition number -> partition info
        self.partDevs = { } # partition number -> active Indigo partition device
        self.partKeysById = { } # partition device ID -> partition number
        
        # Touchpads don't actually have any of their own internal
        # data; they just mirror their configured partition.  To aid
        # that, we will attach touchpad display information to the
        # internal partition state.
        self.touchpadDevs = {
            1: {
                "name": "Touchpad"
            }
        } # partition number -> (touchpad device ID -> Indigo touchpad device)

        # We maintain a regular event log, and an 'error' event log
        # with only exception-type information.  Each has an
        # associated number of days for which it retains events (from
        # oldest to most recent event in log).
        #
        # These are logs of events kept internally as
        # opposed to the log messages which are printed
        # and controlled by the 'log level'
        self.eventLog = deque()
        self.errLog = deque()
        self.eventLogDays = 0
        self.errLogDays = 0

    #
    # Internal event log
    #
    def _logEvent(self, eventInfo, eventTime, q, maxAge):
        pair = (eventTime, eventInfo)
        q.append(pair)
        while len(q) > 0:
            dt = eventTime - q[0][0]
            if dt.days > maxAge:
                q.popleft()
            else:
                break

    def logEvent(self, eventInfo, isErr=False):
        event_time = datetime.datetime.now()
        self._logEvent(eventInfo, event_time, self.eventLog, self.eventLogDays)

        # Send an e-mail if we're armed and we have a zone update
        # This would mean the alarm has detected something
        if self.armed and 'zone_name' in eventInfo and len(eventInfo['zone_state']) > 0:
            email_subject = "--- ALARM EVENT: ZONE " + eventInfo['zone_name']
            email_message = "NEW STATE: " + str(eventInfo['zone_state']) + "\nPREVIOUS STATE: " + str(eventInfo['prev_zone_state']) + "\nCOMMAND: " + str(eventInfo['command'] + "\nDATE: " + str(event_time))
            log.info("Sending Email... ")
            log.debug("Email Contents:" + email_subject + "\n" + email_message)
            send_email(config.EMAILSENDER.decode('base64'), config.EMAILPASSWORD.decode('base64'), config.EMAILRECIPIENT.decode('base64'), email_subject, email_message)

            # Update MQTT
            updateStateOnMQTT('alarm','triggered')

        if isErr:
            self._logEvent(eventInfo, event_time, self.errLog, self.errLogDays)

    def logEventZone(self, zoneName, zoneState, prevZoneState, logMessage, cmd, cmdData, isErr=False):
        d = { 'zone_name': zoneName,
              'zone_state': zoneState,
              'prev_zone_state': prevZoneState,
              'message': logMessage,
              'command': cmd,
              'command_data': cmdData }
        self.logEvent(d, isErr)

    def updateStateOnMQTT(self, topic, value):
        # Store message in queue for main thread to deal with
        mqttMessageQueue.append({'topic':topic,'value':value})

    def updateStateOnServer(self,item,variable,state):
        log.debug(str(item)+' | '+str(variable)+':'+str(state))
        if 'panel' in item:
            log.info('Panel Information: '+str(variable)+': '+str(state))
        if 'touchpad' in item:
            pass
        if 'zone' in item:
            pass

    def startup(self):
        try:
            self.panel = concord.AlarmPanelInterface(self.serialPortUrl, 0.5, log)
        except Exception, ex:
            self.updateStateOnServer("panel","state", "faulted")
            log.error("Unable to start alarm panel interface: %s" % str(ex))
            return

        self.updateStateOnServer('panel','panelState', 'connecting')

        # Set the plugin object to handle all incoming commands
        # from the panel via the messageHandler() method.
        self.panel_command_names = { } # code -> display-friendly name
        for code, cmd_info in concord_commands.RX_COMMANDS.iteritems():
            cmd_id, cmd_name = cmd_info[0], cmd_info[1]
            self.panel_command_names[cmd_id] = cmd_name
            self.panel.register_message_handler(cmd_id, self.panelMessageHandler)

        self.refreshPanelState("Concord 4 panel device startup")

    def refreshPanelState(self, reason):
        """
        Ask the panel to tell us all about itself.  We do this on
        startup, and when the panel asks us to (e.g. under various
        error conditions, or even just periodically).
        """
        log.info("Querying panel for state (%s)" % reason)
        self.updateStateOnServer("panel","state", "exploring")
        self.panel.request_all_equipment()
        self.panel.request_dynamic_data_refresh()
        self.panelInitialQueryDone = False
        

    def isReadyToArm(self, partition_num=1):
        """ 
        Returns pair: first element is True if it's ok to arm;
        otherwise the first element is False and the second element is
        the (string) reason why it is not possible to arm.
        """
        if self.panel is None:
            return False, "The panel is not active"

        # TODO: check all the zones, etc.
        return True, "Partition ready to arm"

    def send_key_press(self,code=[],partition_num=1):
        try:
            self.panel.send_keypress(code, partition_num)
        except Exception, ex:
            log.error("Problem trying to send key=%s" % \
                                  (str(code)))
            log.error(str(ex))
            return False

    def ArmDisarm(self, action='stay', arm_silent = True, bypasszone='',partition_num=1):
        log.debug("Menu item: Arm/Disarm: %s" % str(action))

        errors = {}

        log.info("Concord4 Arm/Disarm to %s, bypass=%s, silent=%s" % (action, str(bypasszone), str(arm_silent)))

        can_arm, reason = self.isReadyToArm(partition_num)
        if not can_arm:
            errors['partition'] = reason
            log.error('Panel not ready to arm')

        if self.panel is None:
            errors['partition'] = "The alarm panel is not active"

        if len(errors) > 0:
            return False, errors

        keys = [ ]
        if arm_silent and 'disarm' not in action:
            keys += KEYPRESS_SILENT
        if action == 'stay':
            if not arm_silent:
                keys += KEYPRESS_ARM_STAY_LOUD
            else:
                keys += KEYPRESS_ARM_STAY
        elif action == 'away':
            if not arm_silent:
                keys += KEYPRESS_ARM_AWAY_LOUD
            else:
                keys += KEYPRESS_ARM_AWAY
        elif action == 'disarm':
            keys += KEYPRESS_DISARM
        else:
            pass

            #assert False, "Unknown arming action type"

        if bypasszone:
            keys += KEYPRESS_BYPASS
        try:
            self.panel.send_keypress(keys, partition_num)
        except Exception, ex:
            log.error("Problem trying to arm action=%s, silent=%s, bypass=%s" % \
                                  (action, str(arm_silent), str(bypasszone)))
            log.error(str(ex))
            errors['partition'] = str(ex)
            return False, errors
        
        return True


    def strToCode(self, s):
        if len(s) != 4:
            raise ValueError("Too short, must be 4 characters")
        v = [ ]
        for c in s:
            n = ord(c) - ord('0')
            if n < 0 or n > 9:
                raise ValueError("Non-numeric digit")
            v += [ n ]
        return v


    def getPartitionState(self, part_key):
        #assert part_key in self.parts
        part_data = self.parts[part_key]
        arm_level = part_data.get('arming_level_code', -1)
        part_state = PART_ARM_STATE_MAP.get(arm_level, 'unknown')
        return part_state
    
    def updateTouchpadDeviceState(self, touchpad_dev, part_key):
        if part_key not in self.parts:
            log.debug("Unable to update touchpad device %s - partition %d; no knowledge of that partition" % (touchpad_dev.name, part_key))
            self.updateStateOnServer('touchpad','partitionState', 'unknown')
            self.updateStateOnServer('touchpad','lcdLine1', NO_DATA)
            self.updateStateOnServer('touchpad','lcdLine2', NO_DATA)
            return

        part_data = self.parts[part_key]
        lcd_data = part_data.get('display_text', '%s\n%s' % (NO_DATA, NO_DATA))
        # Throw out the blink information.  Not sure how to handle it.
        lcd_data = lcd_data.replace('<blink>', '')
        lines = lcd_data.split('\n')
        if len(lines) > 0:
            self.updateStateOnServer('touchpad','lcdLine1', lines[0].strip())
        else:
            self.updateStateOnServer('touchpad','lcdLine1', NO_DATA)
        if len(lines) > 1:
            self.updateStateOnServer('touchpad','lcdLine2', lines[1].strip())
        else:
            self.updateStateOnServer('touchpad','lcdLine2', NO_DATA)
        self.updateStateOnServer('touchpad','partitionState', self.getPartitionState(part_key))

        # Info out the current touchpad display text, but only for partition 1.
        if part_key == 1:
            display_text_message = lines[0].strip()
            if len(lines) > 1:
                display_text_message += " " + lines[1].strip()
            log.info("Latest touchpad display text: '" + display_text_message + "'")

    def updatePartitionDeviceState(self, part_dev, part_key):
        if part_key not in self.parts:
            log.debug("Unable to update partition device %s - partition %d; no knowledge of that partition" % (part_dev.name, part_key))
            self.updateStateOnServer('partition','partitionState', 'unknown')
            self.updateStateOnServer('partition','armingUser', '')
            self.updateStateOnServer('partition','features', 'Unknown')
            self.updateStateOnServer('partition','delay', 'Unknown')
            return

        part_state = self.getPartitionState(part_key)
        part_data = self.parts[part_key]
        arm_user  = part_data.get('user_info', 'Unknown User')
        features  = part_data.get('feature_state', ['Unknown'])

        delay_flags = part_data.get('delay_flags')
        if not delay_flags:
            delay_str = "No delay info"
        else:
            delay_str = "%s, %d seconds" % (', '.join(delay_flags), part_data.get('delay_seconds', -1))
        self.updateStateOnServer('partition','partitionState', part_state)
        self.updateStateOnServer('partition','armingUser', arm_user)
        self.updateStateOnServer('partition','features', ', '.join(features))
        self.updateStateOnServer('partition','delay', delay_str)


    # Will be run in the concurrent thread.
    def panelMessageHandler(self, msg):
        """ *msg* is dict with received message from the panel. """
        cmd_id = msg['command_id']

        # Log about the message, but not for the ones we hear all the
        # time.  Chatterbox!
        if cmd_id in ('TOUCHPAD', 'SIREN_SYNC'):
            # These message come all the time so only print about them
            # if the user signed up for extra verbose debug logging.
            log_fn = log.debug
        else:
            log_fn = log.debug
        log_fn("Handling panel message %s, %s" % \
                   (cmd_id, self.panel_command_names.get(cmd_id, 'Unknown')))

        #
        # First set of cases by message to update plugin and device state.
        #
        if cmd_id == 'PANEL_TYPE':
            self.updateStateOnServer('panel','panelType', msg['panel_type'])
            self.updateStateOnServer('panel','panelIsConcord', msg['is_concord'])
            self.updateStateOnServer('panel','panelSerialNumber', msg['serial_number'])
            self.updateStateOnServer('panel','panelHwRev', msg['hardware_revision'])
            self.updateStateOnServer('panel','panelSwRev', msg['software_revision'])
            #self.updateStateOnServer('panel','panelZoneMonitorEnabled', self.zoneMonitorEnabled)
            #self.updateStateOnServer('panel','panelZoneMonitorSendEmail', self.zoneMonitorSendEmail)

        elif cmd_id in ('ZONE_DATA', 'ZONE_STATUS'):
            # First update our internal state about the zone
            zone_num = msg['zone_number']
            part_num = msg['partition_number']
            zk = zone_num
            zone_name = '%d' % zone_num

            old_zone_state = "Not known"
            new_zone_state = msg['zone_state']

            if zk in self.zones:
                log.debug("Updating zone %s with %s message, zone state=%r" % \
                                     (zone_name, cmd_id, msg['zone_state']))
                zone_info = self.zones[zk]
                old_zone_state = zone_info['zone_state']
                zone_info.update(msg)
                del zone_info['command_id']
            else:
                log.debug("Learning new zone %s from %s message, zone_state=%r" % \
                                     (zone_name, cmd_id, msg['zone_state']))
                zone_info = msg.copy()
                del zone_info['command_id']
                self.zones[zk] = zone_info

            # Set zone text to friendly text if none is there
            if not 'zone_text' in zone_info or zone_info['zone_text'] == '':
                zone_info['zone_text'] = FRIENDLY_ZONE_NAME_MAP[zk]

            # Determine the zone name friendly if possible
            if 'zone_text' in msg and msg['zone_text'] != '':
                zone_name = '%s - %r' % (zone_num, msg['zone_text'])
            elif zk in self.zones and self.zones[zk].get('zone_text', '') != '':
                zone_name = '%s - %r' % (zone_num, self.zones[zk]['zone_text'])

            # Next sync up any devices that might be for this
            # zone.
            if len(new_zone_state) == 0:
                zs = 'closed'
                self.updateStateOnMQTT('zone/'+str(zone_num),'closed')
            elif FAULTED in new_zone_state or TROUBLE in new_zone_state:
                zs = 'faulted'
            elif ALARM in new_zone_state:
                zs = 'alarm'
                self.updateStateOnMQTT('zone/'+str(zone_num),'open')
            elif TRIPPED in new_zone_state:
                zs = 'open'
                delay = 0
                zone = 'zone'+str(zone_num)
                self.updateStateOnMQTT('zone/'+str(zone_num),'open')
            elif BYPASSED in new_zone_state:
                zs = 'disabled'
            else:
                zs = 'unavailable'

            log.info('Zone '+zone_name + ' | State: '+zs)
            self.updateStateOnServer('zone',str(zone_num),zs)

            # Log to internal event log.  If the zone is changed to or
            # from one of the 'error' states, we will use the error
            # log as well.  We don't normally have to check for change
            # per se, since we know it was a zone change that prompted
            # this message.  However, if a zone is in an error state,
            # we don't want to log an error every time it is change
            # between tripped/not-tripped.
            use_err_log = (isZoneErrState(old_zone_state) or isZoneErrState(new_zone_state)) \
                and zoneStateChangedExceptTripped(old_zone_state, new_zone_state)
            
            self.logEventZone(zone_name, new_zone_state, old_zone_state,
                              "Zone update message", cmd_id, msg, use_err_log)

        elif cmd_id in ('ARM_LEVEL'):
            if int(msg['arming_level_code']) == 1:
                log.info('System is DISARMED')
                self.armed = False
                self.updateStateOnServer('armstatus','arm_level','disarmed')
                self.updateStateOnMQTT('alarm','disarmed')
            elif int(msg['arming_level_code']) == 2:
                log.info('System is ARMED to STAY')
                self.armed = True
                self.updateStateOnServer('armstatus','arm_level','armed_stay')
                self.updateStateOnMQTT('alarm','armed_home')
            elif int(msg['arming_level_code']) == 3:
                log.info('System is ARMED to AWAY')
                self.armed = True
                self.updateStateOnServer('armstatus','arm_level','armed_away')
                self.updateStateOnMQTT('alarm','armed_away')

        elif cmd_id in ('PART_DATA', 'FEAT_STATE', 'DELAY', 'TOUCHPAD'):
            part_num = msg['partition_number']
            old_part_state = "Unknown"
            if part_num in self.parts:
                old_part_state = self.getPartitionState(part_num)
                # Log informational message about updating the
                # partition with message info.  However, for touchpad
                # messages this could be quite frequent (every minute)
                # so log at a higher level.
                if cmd_id == 'TOUCHPAD':
                    log_fn = log.debug
                else:
                    log_fn = log.info
                log.debug("Updating partition %d with %s message" % (part_num, cmd_id))
                part_info = self.parts[part_num]
                part_info.update(msg)
                del part_info['command_id']
            else:
                log.info("Learning new partition %d from %s message" % (part_num, cmd_id))
                part_info = msg.copy()
                del part_info['command_id']
                self.parts[part_num] = part_info

            # Update arming level on MQTT if present and we haven't updated it yet
            if self.armed is None:
                armingLevel = msg.get('arming_level')
                armingLevelCode = msg.get('arming_level_code')
                if armingLevel is not None and armingLevelCode is not None:
                    if int(armingLevelCode) == 1:
                        log.info('System is DISARMED')
                        self.armed = False
                        self.updateStateOnMQTT('alarm','disarmed')
                    elif int(armingLevelCode) == 2:
                        log.info('System is ARMED to STAY')
                        self.armed = True
                        self.updateStateOnMQTT('alarm','armed_home')
                    elif int(armingLevelCode) == 3:
                        log.info('System is ARMED to AWAY')
                        self.armed = True
                        self.updateStateOnMQTT('alarm','armed_away')

            if part_num in self.partDevs:
                self.updatePartitionDeviceState(self.partDevs[part_num], part_num)
            else:
                # The panel seems to send touchpad date/time messages
                # for all partitions it supports.  User may not wish
                # to see warnings if they haven't setup the Partition
                # device in Indigo, so log this at a higher level.
                if cmd_id == 'TOUCHPAD':
                    log_fn = log.debug
                else:
                    log_fn = log.warn

            # We update the touchpad even when it's not a TOUCHPAD
            # message so that the touchpad device can track the
            # underlying partition state.  Later on we may also add
            # other features to mirror the LEDs on an actual touchpad
            # as well.
            if part_num in self.touchpadDevs:
                for dev_id, dev in self.touchpadDevs[part_num].iteritems():
                    self.updateTouchpadDeviceState(dev, part_num)

            # Write message to internal log
            if cmd_id in ('PART_DATA', 'ARM_LEVEL', 'DELAY'):
                part_state = self.getPartitionState(part_num)
                use_err_log = cmd_id != 'PART_DATA' or old_part_state != part_state or part_state != 'ready'
                self.logEvent(msg, use_err_log)

        elif cmd_id == 'EQPT_LIST_DONE':
            if not self.panelInitialQueryDone:
                self.updateStateOnServer('panel','state', 'active')
                self.panelInitialQueryDone = True

        elif cmd_id == 'ALARM':
            part_num = msg['partition_number']
            source_type = msg['source_type']
            source_num = msg['source_number']
            alarm_code_str ="%d.%d" % (msg['alarm_general_type_code'], msg['alarm_specific_type_code'])
            alarm_desc = "%s / %s" % (msg['alarm_general_type'], msg['alarm_specific_type'])
            event_data = msg['event_specific_data']

            # We really only care if its of gen type 1 (fire,police, etc)
            if msg['alarm_general_type_code'] == '1':
                zk = (part_num, source_num)
                if source_type == 'Zone' and zk in self.zones:
                    zone_name = self.zones[zk].get('zone_text', 'Unknown')
                    if zk in self.zoneDevs:
                        source_desc = "Zone %d - Zone %s, alarm zone %s" % \
                            (source_num, self.zoneDevs[zk].name, zone_name)
                    else:
                        source_desc = "Zone %d - alarm zone %s" % (source_num, zone_name)
                else:
                    source_desc = "%s, number %d" % (source_type, source_num)
                log.error("ALARM or TROUBLE on partition %d: Source details: %s" % (part_num, source_desc))

                self.updateStateOnServer('panel','state','alarm')


                msg['source_desc'] = source_desc
                self.logEvent(msg, True)

        elif cmd_id in ('CLEAR_IMAGE', 'EVENT_LOST'):
            self.refreshPanelState("Reacting to %s message" % cmd_id)

        else:
            log.debug("Concord: unhandled panel message %s" % cmd_id)


class PanelConcurrentThread(Thread):
    def __init__(self, panel):
        ''' Constructor. '''

        Thread.__init__(self)
        self.panel = panel
        self.StopThread = False
        self.daemon = True

    def run(self):
        try:
            # Run the panel interface event loop.  It's possible for
            # this thread to be running before the panel object is
            # constructed and the serial port is configured.  We have
            # an outer loop because the user may stop the panel device
            # which will cause the panel's message loop to be stopped.
            while True:
                while self.panel is None:
                    time.sleep(1)
                self.panel.message_loop()

        except self.StopThread:
            log.debug("Got StopThread in PanelConcurrentThread")
            pass

class ConcordMQTT(object):

    def __init__(self, config):
        ''' Constructor. '''

        #Store config
        self._config = config
        self.client = None

    def startup(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_log = self.on_log

        self.client.username_pw_set(config.MQTTUSER.decode('base64'),config.MQTTPASSWORD.decode('base64'))

        isConnected = False
        while not isConnected:
            try:            
                self.client.connect(config.HOST, config.PORT, 60)
                isConnected = True
            except:
                logger("MQTT connection failed, trying again in 10 seconds...")
                time.sleep(10)

        # Blocking call that processes network traffic, dispatches callbacks and
        # handles reconnecting.
        # Other loop*() functions are available that give a threaded interface and a
        # manual interface.
        logger('MQTT setup for '+str(config.HOST)+':'+str(config.PORT))
        self.client.loop_start()

    def end(self):
        if self.client is not None:
            self.client.loop_stop()

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(self, client, userdata, flags, rc):

        # Any rc but 0 is a failure to connect
        logger("MQTT connected with result code "+str(rc))

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        client.subscribe("concord/#")

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        logger("RX -> "+msg.topic+" - "+msg.payload)

        # Handle message
        topic = msg.topic.lower()
        value = msg.payload.lower()
        if topic == 'concord/alarm/set':
            if value == 'arm_home':
                concord_interface.ArmDisarm(action='stay')
                logger("MQTT - Arming System to STAY...")
            elif value == 'arm_away':
                concord_interface.ArmDisarm(action='away')
                logger("MQTT - Arming System to AWAY...")
            elif value == 'disarm':
                concord_interface.ArmDisarm(action='disarm')
                logger("MQTT - Disarm System...")

    # The callback for logging
    def on_log(self, client, userdata, level, buf):
        log.debug("MQTT LOG - " + str(buf))

if __name__ == '__main__':
    args = sys.argv[1:]

    print('Concord 4 MQTT Automation Server v' +str(version))

    config = Concord4ServerConfig('concordsvr_mqtt.conf')
    start_logger()

    mqttMessageQueue = []

    concord_interface = ConcordSvr()
    concord_interface.startup()
    concord_mqtt = ConcordMQTT(config)
    concord_mqtt.startup()
    concord_panel_thread = PanelConcurrentThread(concord_interface.panel)
    concord_panel_thread.start()

    try:
        while True:

            # Use a message queue to deliver MQTT messages
            # There's some sort of bug in the MQTT library and a different thread will never send publish messages for some reason
            if len(mqttMessageQueue) == 0:
                time.sleep(1)
            else:
                for message in mqttMessageQueue:
                    send_mqtt_update(message['topic'],message['value'])
                mqttMessageQueue = []

    except KeyboardInterrupt:
        print "Crtl+C pressed. Shutting down."
        logger('Shutting down from Ctrl+C')
        concord_mqtt.end()
        concord_panel_thread.panel = None
        concord_panel_thread.StopThread = True
        sys.exit()