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

class Track(object):
	def __init__(self, maxPosition = 16):
		self.ranges = []
		self._maxPosition = maxPosition
	
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
tracks = []
tempo = 0
measureLength = 0
_jackRunning = False

mapping = {
0: {
	'start': {'event': 'note', 'param': 100},
	'stop': {'event': 'note', 'param': 100},
	'channel': 0
},
1: {
	'start': {'event': 'note', 'param': 101},
	'stop': {'event': 'note', 'param': 101},
	'channel': 0
},
2: {
	'start': {'event': 'note', 'param': 102},
	'stop': {'event': 'note', 'param': 102},
	'channel': 0
},
3: {
	'start': {'event': 'note', 'param': 103},
	'stop': {'event': 'note', 'param': 103},
	'channel': 0
},
4: {
	'start': {'event': 'note', 'param': 101},
	'stop': {'event': 'note', 'param': 101},
	'channel': 0
}}

def beat(tick):
	if not ((tick + 1) % (2 * measureLength)):
		# tick before measure start...
		for i in range(len(tracks)):
			# get current track
			itrack = tracks[i]
			if i in mapping:
				imap = mapping[i]
			elif str(i) in mapping:
				imap = mapping[str(i)]
			else:
				# track is not mapped
				continue

			# send MIDI events
			if itrack.ranges:
				try:
					if itrack.ranges[0][0] == 0:
						# ready to fire start event
						if imap['start']['event'] == 'note':
							seq.event_write(midi.NoteOnEvent(pitch=imap['start']['param'], channel=imap['channel'], velocity=64), direct=True)
					if itrack.ranges[0][1] == 0:
						# ready to fire stop event
						if imap['stop']['event'] == 'note':
							seq.event_write(midi.NoteOnEvent(pitch=imap['stop']['param'], channel=imap['channel'], velocity=64), direct=True)
				except KeyError:
					print 'ERROR: Please check mapping for track #%s: %s' % (i, imap)

		# advance tracks
		for itrack in tracks:
			itrack.advance()

	if not (tick % 2):
		# show LEDs
		for i in range(len(tracks)):
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
			for itrack in tracks:
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

def init(device, alsaClients, _tempo, _measureLength):
	'''Initiate all variables an devices/classes.'''
	global seq, mon, buttons, tracks, tempo, measureLength
	tempo = _tempo
	measureLength = _measureLength

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

	# init tracks
	tracks = [Track(mon.columns) for i in range(mon.rows)]

	# start main loop
	loop()

def parse():
	'''Parse the command line arguments.'''
	if '-L' in sys.argv or '--list' in sys.argv:
		print midi.sequencer.SequencerHardware()
		sys.exit(0)

	parser = argparse.ArgumentParser(description='A live midi sequencer using the monome.')
	parser.add_argument('--device', '-d', metavar='dev', required=True, help='Path to the USB devices corresponding to your monome (e.g. /dev/ttyUSB0)')
	parser.add_argument('--tempo', '-t', metavar='bpm', default=120, type=int, help='Tempo for the session (in bpm). (default=120)')
	parser.add_argument('--measure-length', '-l', metavar='length', default=4, type=int, help='Length of each measure (in quarter notes). (default=4)')
	parser.add_argument('--client', '-c', metavar='client', required=True, action='append', help='ALSA client to connect to, optional with a port after a colon (e.g. "Hydrogen:Hydrogen Midi-In", "Hydrogen:0", "130:0", "Hydrogen", or "130").')
	parser.add_argument('--port', '-p', metavar='port', default=0, help='ALSA client port to connect to (e.g. "Hydrogen Midi-In" or "0"). (default=0)')
	parser.add_argument('--list', '-L', action='store_const', const=True, help='Lists available ALSA clients and ports')
	args = parser.parse_args()

	init(args.device, args.client, args.tempo, args.measure_length)

if __name__ == "__main__":
	parse()
