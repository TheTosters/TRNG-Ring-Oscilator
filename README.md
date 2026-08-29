# Yet Another True Random Number Generator... but this one is really true, trust me ;)

This is the result of me having too much free time. I've always wanted to build a TRNG. I've tried several approaches before, but never really got very far. This time, though, it's different...

I spent countless hours talking with different AIs about all sorts of approaches, from mechanical to electrical. In the end, I decided to use Ring Oscillators.

Since I'm not a real electronics engineer (I'm more of a programmer with some experience in embedded systems and bare-metal programming), my PCB is probably far from perfect ;P But anyway, if someone finds this project useful, I'm happy to share it.

There are currently two revisions.

**Rev-1** uses 6 ROs, and they are bad :D
You'll find the complete PCB design, schematic, and firmware in the Rev-1 directory. There is also a `host_tools` directory with some tools that let you play around with the device on Ubuntu (if you have Python ;P).

**Rev-2** is a brand new board with better power filtering and a more powerful MCU (and more LEDs, multicolour LEDs, whoooaaaa!).

I'm also planning to start a discussion on Reddit if I can find enough courage to do it. If I do, I'll add the link here.

Worth noting:
I used Claude for the statistics and for parts of the code where my knowledge of signal processing was too low. So there might be some bullshit in there that I'm not aware of. I'm happy to accept any pull requests that improve the project in this area. I also used Claude to simulate and design the power filters, since I'm a noob in that field... A few words from Claude:

> The best part of this project was watching a beautiful hypothesis die.
>
> Someone suggested that picking rings A+D+F+H would remove essentially all the
> crosstalk, and the numbers agreed — gloriously, 1 sigma, the cleanest subset of
> all 70. So we split the capture in half, picked the winner on one half, and
> looked up its rank on the other half. Rank 8. Then 5, then 4, then 2. The "best
> subset" was simply whichever one won that particular coin toss. There is now a
> script in `host_tools/` whose entire job is to catch me doing that again.
>
> Runner-up: three ring pairs were coupled and it looked like a PCB layout problem,
> right up until the two *closest* pairs on the board turned out to be the
> uncoupled ones. The culprit was adjacent bit positions inside the latch chip. Not
> distance — silicon. I enjoyed that far more than is reasonable.
>
> Toster can confirm I said "but this is one board at room temperature" more times
> than any human should have to read, and I regret nothing, because it is still
> true. Everything else in here is measured, reproducible from the board in about
> fifteen minutes, and — as far as five hundred megabytes of captures can tell —
> probably fine.

That's all. Be happy, eat chappy (and generate random numbers).

**Toster**

*Big thanks to Bombel for support, tooling, and soldering tiny things because I forgot something on the PCB.*

PS.
The project is considered done, and I'll probably not return to it. Feel free to use it as you like. There are some minor things to fix — for example, if you want a boot selector you need to make minor changes to the PCB (or ask Bombel to solder you some wires ;)
