#!/usr/bin/env python

import sys
import math
import midi
import midi.sequencer
import jack
import monome
from threading import Timer
import time
import argparse
import ConfigParser
import re
import logging

class Track(object):
	def __init__(self, maxPosition = 16, startEvent = None, stopEvent = None, channel = -1, tickOffset = 0):
		self.ranges = []
		self._maxPosition = maxPosition
		self.startEvent = startEvent
		self.stopEvent = stopEvent
		self.channel = channel
		self.tickOffset = tickOffset
	
	def add(self, pos):
		'''Add a new position that has been pressed and integrate it into
		the existing ranges.'''
		inRange = False
		for irange in self.ranges:
			inRange = True

			if pos == irange[0]:
				# position fits beginning of range -> delete range
				self.ranges.remove(irange)
			elif pos > irange[0] and (pos < irange[1] or -1 == irange[1]):
				# resize range
				if irange[1] == -1:
					# end is unlimited -> position specifies new end
					irange[1] = pos
				elif pos - irange[0] < irange[1] - pos:
					# position is closer to range start -> modify start
					irange[0] = pos
				else:
					# position is closer to range end -> modify end
					irange[1] = pos
			else:
				# position could not be matched
				inRange = False

		if not inRange:
			# position could not be matched -> add a new range
			newRange = [pos, -1]

			for irange in reversed(self.ranges):
				if pos < irange[0]:
					# new range is limited by existing range -> merge into existing range
					irange[0] = pos
					newRange = None
					break

			if newRange:
				self.ranges.append(newRange)

		# clean up... i.e., merge subsequent ranges
		i = 1
		while i < len(self.ranges):
			if self.ranges[i - 1][1] == self.ranges[i][0]:
				self.ranges[i - 1][1] = self.ranges[i][1]
				del self.ranges[i]
			else:
				i += 1

	def advance(self):
		'''Advance all ranges by one position.'''
		i = 0
		while i < len(self.ranges):
			irange = self.ranges[i]
			if irange[0] <= 0 and irange[1] == -1:
				# special handling for endless range
				irange[0] = -1
				i += 1
				continue

			irange[0] = max(-1, irange[0] - 1)
			irange[1] = max(-1, irange[1] - 1)
			if irange[0] < 0 and irange[1] < 0:
				del self.ranges[i]
			else:
				i += 1

	@property
	def mask(self):
		'''Returns bit mask for the track.'''
		mask = 0
		for irange in self.ranges:
			start = max(0, irange[0])
			end = irange[1]
			if end < 0:
				end = self._maxPosition
			for i in range(start, end):
				mask |= 1 << i
		return mask

	def clear(self):
		'''Clears all track ranges.'''
		self.ranges = []

seq = None
mon = None
buttons = [[]]
tracks = {}
tempo = 0
measureLength = 0
_jackRunning = False

_regMidiEvent = re.compile(r'(?P<type>note|pc)(?P<param>[0-9]+)')
def str2midiEvent(s, channel):
	'''Convert a given MIDI event string to a real MIDI event.'''
	m = _regMidiEvent.match(s)
	if not m:
		return None

	try:
		# parse the string information
		param = int(m.group('param'))
		if m.group('type') == 'note':
			# return a note on event
			return midi.NoteOnEvent(pitch=param, channel=channel, velocity=64)
		elif m.group('type') == 'pc':
			# return a program change event
			return midi.ProgramChangeEvent(data=[param], channel=channel)
		else:
			return None
	except ValueError:
		return None

def beat(tick):
	# tick before measure start...
	# send MIDI events
	logging.debug('tick: %s' % tick)
	for i, itrack in tracks.iteritems():
		if not ((tick - itrack.tickOffset) % (2 * measureLength)):
			if itrack.ranges:
				# pending events...
				evt = None
				if itrack.ranges[0][0] == 0:
					# ready to fire start event
					evt = str2midiEvent(itrack.startEvent, itrack.channel)
				if itrack.ranges[0][1] == 0:
					# ready to fire stop event
					evt = str2midiEvent(itrack.stopEvent, itrack.channel)
				
				# send event directly if applicable
				if evt:
					logging.debug('sending event: %s' % evt)
					seq.event_write(evt, direct=True)

	if not (tick % (2 * measureLength)):
		# advance tracks
		for i, itrack in tracks.iteritems():
			itrack.advance()

	if not (tick % 2):
		# show LEDs
		for i, itrack in tracks.iteritems():
			mask = tracks[i].mask
			mon.led_row(0, i, mask)
	else:
		# uneven tick, switch off all LEDs
		mon.led_all(0)

def readEvents(tick):
	'''Process all monome events.'''
	while True:
		e = mon.next_event()
		if not e: break  # stop if all events have been processed
		if not e.pressed: continue  # ignore button-up events

		# add pressed position to the corresponding track
		track = tracks[e.y]
		track.add(e.x)

def loop():
	'''Main loop function, calls processing sub-functions.'''
	global _jackRunning

	# compute the current tick
	interval = 60.0 / tempo / 2  # call the loop twice per beat (for blinking)
	transportTime = float(jack.get_current_transport_frame()) / jack.get_sample_rate()
	tickCount = math.floor(transportTime / interval)

	if tickCount > 0 and not _jackRunning:
		print 'jack is up and running... starting monome sequencer'
		_jackRunning = True

	if jack.get_transport_state() != jack.TransportRolling:
		# jack is stopped... stop processing here, as well
		if tickCount == 0 and _jackRunning:
			print 'jack transport has been stopped... waiting for jack to be restarted'
			_jackRunning = False
			mon.led_all(0)
			for i, itrack in tracks.iteritems():
				itrack.clear()
		Timer(0.1, loop).start()
		return

	# processing
	readEvents(tickCount)
	beat(tickCount)

	# see when the loop needs to be called the next time
	transportTime = float(jack.get_current_transport_frame()) / jack.get_sample_rate()
	waitTime = (tickCount + 1) * interval - transportTime
	Timer(waitTime, loop).start()

def init(ini, device, alsaClients, _tempo, _measureLength, debugLevel):
	'''Initiate all variables an devices/classes.'''
	global seq, mon, buttons, tracks, tempo, measureLength
	tempo = _tempo
	measureLength = _measureLength

	# init logging
	numeric_level = getattr(logging, debugLevel.upper(), None)
	if not isinstance(numeric_level, int):
		raise ValueError('Invalid log level: %s' % loglevel)
	logging.basicConfig(level=numeric_level)

	# initiate MIDI sequencer
	seq = midi.sequencer.SequencerWrite(alsa_sequencer_name='moseq', alsa_port_name='moseq in', alsa_queue_name='moseq queue')
	hardware = midi.sequencer.SequencerHardware()
	for iclient in alsaClients:
		# split string at ':'
		tmp = iclient.split(':')
		if len(tmp) <= 1:
			port = '0'
		else:
			port = tmp[1]
		client = tmp[0]

		try:
			# try to parse integers
			port = int(port)
			try:
				client = int(client)
			except ValueError:
				# fallback: handle named client name
				client = hardware.get_client(client).client
		except ValueError:
			# fallback: handle named client and port
			client, port = hardware.get_client_and_port(client, port)
		seq.subscribe_port(client, port)
	seq.start_sequencer()

	# init monome
	mon = monome.Monome(device)
	mon.led_all(0)
	buttons = [[0 for x in range(mon.columns)] for y in range(mon.rows)]

	# init connection to jack
	jack.attach('moseq')

	# read in config file and init tracks
	parser = ConfigParser.SafeConfigParser()
	parser.read(ini)
	regSection = re.compile(r'track(?P<track>[0-9]+)')
	for isec in parser.sections():
		m = regSection.match(isec)
		if not m:
			# no track configuration section... ignore
			continue

		try:
			# get track number
			i = int(m.group('track'))

			# init a new Track
			vals = dict(parser.items(isec))
			tracks[i] = Track(mon.columns,
					startEvent=vals.get('start'),
					stopEvent=vals.get('stop'),
					channel=int(vals.get('channel', -1)),
					tickOffset=vals.get('tickOffset', 0))
		except ValueError:
			continue
		
	# start main loop
	print 'Starting main loop...'
	loop()

def parse():
	'''Parse the command line arguments.'''
	if '-L' in sys.argv or '--list' in sys.argv:
		print midi.sequencer.SequencerHardware()
		sys.exit(0)

	parser = argparse.ArgumentParser(description='A live midi sequencer using the monome.')
	parser.add_argument('--ini-file', '-i', metavar='ini', required=True, help='Path to a configuration .ini file.')
	parser.add_argument('--device', '-d', metavar='dev', required=True, help='Path to the USB devices corresponding to your monome (e.g. /dev/ttyUSB0)')
	parser.add_argument('--tempo', '-t', metavar='bpm', default=120, type=int, help='Tempo for the session (in bpm). (default=120)')
	parser.add_argument('--measure-length', '-l', metavar='length', default=4, type=int, help='Length of each measure (in quarter notes). (default=4)')
	parser.add_argument('--client', '-c', metavar='client', required=True, action='append', help='ALSA client to connect to, optional with a port after a colon (e.g. "Hydrogen:Hydrogen Midi-In", "Hydrogen:0", "130:0", "Hydrogen", or "130").')
	parser.add_argument('--port', '-p', metavar='port', default=0, help='ALSA client port to connect to (e.g. "Hydrogen Midi-In" or "0"). (default=0)')
	parser.add_argument('--list', '-L', action='store_const', const=True, help='Lists available ALSA clients and ports')
	parser.add_argument('--debug', '-D', metavar='level', choices=set(('info', 'debug', 'warn', 'critical', 'error')), default='critical', help='define the debug level: info, debug, warn, critical, error')
	args = parser.parse_args()

	init(args.ini_file, args.device, args.client, args.tempo, args.measure_length, args.debug)

if __name__ == "__main__":
	parse()
