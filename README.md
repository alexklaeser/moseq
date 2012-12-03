MoSeq
=====
Tool for using the monome as a multi track MIDI sequencer for a live loop
setup.  That is where the name is coming from: Monome Sequencer - MoSeq.

MoSeq is written in Python, developed under Linux, with a 8x8 monome,
and it uses the following libraries:
* [python-midi](https://github.com/vishnubob/python-midi) - for reading and writing MIDI events
* [libmonome](https://github.com/monome/libmonome/) - for interfacing the monome device
* [pyjack](http://sourceforge.net/projects/py-jack/) - in order to listen to start/stop events of the jack server and to synchronize to jack

The project just started, it already works minimalistically, yet still more
work remains to be done.

About MoSeq
-----------
In order to get an idea what MoSeq should be able to do, imagine a 
[live looping setup](http://en.wikipedia.org/wiki/Live_looping), e.g.,
with [Hydrogen](http://www.hydrogen-music.org) and 
[SooperLooper](essej.net/sooperlooper) on a Linux platform.
Now, wouldn't it be nice to be able to control and arrange the different 
loop tracks ahead of the actual point in time when they are being
played?

MoSeq can do exactly this! Rows of the monome are used to visualize the
current tracks that are being played. Each column refers to one measure
(or more if wished) such that a track can be triggered to play in x
measures in the future. Monome LEDs are blinking in the specified rhythm
(aligned to the Jack server) and are visualizing which track is played when
in time.

As far as I know, MoSeq is the only native Monome application on Linux.
Other applications are developed
within the [Max framework](http://cycling74.com). I followed the setup described 
[on the official monome wiki page](http://docs.monome.org/doku.php?id=setup:linux)
to run monome applications on my Linux computer, however, I really was not
happy with using a proprietary framework for using/writing apps which would not
even support Linux as platform.

Features
--------
* synchronization to Jack, listen to start/stop events of Jack
* tracks can be pre-arranged on the fly, MIDI events are triggered to start and stop loops
* tempo can be specified via a command line option

About Me
--------
You can check out my personal webpage at: http://alexklaeser.de
