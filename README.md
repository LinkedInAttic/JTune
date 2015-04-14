# JTune - a high precision Java CMS optimizer

Overview
--------

JTune is a tool that will help you tune and troubleshoot a running JVM (Java 6 - Java 8) without restarting. It currently doesn't work with the G1 garbage collector, and will error out if this is detected. Tuning is based on two metrics: the aggregate time spent doing GCs, and the standard deviation of the GCs. Upon invocation, JTune captures the output of jstat for the given pid as well as the GC log data during the sample period.

Options Help
------------

In normal use, run JTune with the -p \<pid> parameter, and it will run indefinitely.  When you are ready, hit CTRL-C, and detailed information about its findings will be printed, along with recommendations for improvement (if any).

There are additional options that you can take advantage of. See below output:
    
    $ jtune.py -h
    usage: jtune.py [-h] [-o OPTIMIZE] [-P] [-s FGC_STOP_COUNT]
                     [-y YGC_STOP_COUNT] [-c STOP_COUNT] [-n] (-r [FILE] | -p PID)
    
    Run jstat w/ analytics
    
    optional arguments:
      -h, --help            show this help message and exit
      -o OPTIMIZE, --optimize OPTIMIZE
                            Optimize for latency or throughput (range 0-11, 0 =
                            ygc @ 180/min, 11 = ygc @ 1/min). Floats allowed.
      -P, --no-paste        Don't save the screen output to the paste service
      -s FGC_STOP_COUNT, --fgc-stop-count FGC_STOP_COUNT
                            How many full gcs should happen before I stop (very
                            important for analytics)
      -y YGC_STOP_COUNT, --ygc-stop-count YGC_STOP_COUNT
                            How many young gcs should happen before I stop
      -c STOP_COUNT, --stop-count STOP_COUNT
                            How many iterations of jstat to run before stopping
      -n, --no-jstat-output
                            Do not show jstat output - only print summary
      -r [FILE], --replay [FILE]
                            Replay a previously saved default is
                            /tmp/jtune_data-{user}.bin.bz2 file
      -p PID, --pid PID     Which java PID should I attach to

* You can also have it stop after X number of YGCs, FGCs, or jstat iterations (-y, -s, -c respectively). If you want it to make tuning suggestions, you'll want to let it run for at least 3 FGCs (-s <#>) before exiting.
* There may be cases where you want jtune to optimize for a given number of CMS GCs, you can do this with the '-o #' parameter. Right now you can specify a range between 0 and 11 which corresponds to the 180 CMS/min to 1 CMS/min respectively. In most cases you can leave it as default. The way this parameter is used will likely change.
* There may be cases where you see something odd in the suggestions, or want to save the data structures jtune.py used for further analysis. By default jtune saves this data in /tmp/jtune_data-{user}.bin.bz2. JTune can replay this file by passing it a -r \<path\> parameter.

Command Output
--------------

Here's an example of a JTune run for a test instance (broken up into chunks)

* JTune is running against PID 25815 for 40 iterations, exit, and report its findings:

```
$ jtune.py -c 40 -p 25815
#####
# Start Time:  2015-03-23 12:31:45.079102 GMT
# Host:        fake-host.linkedin.com
#####
   EC      EP      EU  S0C/S1C     S0U     S1U      OC      OP      OU     MC     MU    YGC  YGCD  FGC  FGCD
~~~~~~  ~~~~~~  ~~~~~~  ~~~~~~~  ~~~~~~  ~~~~~~  ~~~~~~  ~~~~~~  ~~~~~~  ~~~~~  ~~~~~  ~~~~~  ~~~~  ~~~  ~~~~
  1.1G   96.0%    1.1G   117.3M   76.5M      0K   13.6G   50.6%    6.9G    90M  88.2M  71876     -  138     -
  1.1G   28.0%    329M   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877    +1  138     -
  1.1G   51.5%  604.7M   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877     -  138     -
  1.1G   55.1%  646.4M   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877     -  138     -
  1.1G   74.5%  874.6M   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877     -  138     -
  1.1G   88.1%      1G   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877     -  138     -
  1.1G   92.5%    1.1G   117.3M      0K    9.5M   13.6G   50.7%    6.9G    90M  88.2M  71877     -  138     -
  1.1G   15.6%  182.8M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878    +1  138     -
  1.1G   50.3%  589.7M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878     -  138     -
  1.1G   60.8%  713.8M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878     -  138     -
  1.1G   65.4%  767.2M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878     -  138     -
  1.1G   66.0%  774.7M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878     -  138     -
  1.1G   78.6%  922.5M   117.3M     17M      0K   13.6G   50.7%    6.9G    90M  88.2M  71878     -  138     -
  1.1G    5.1%   59.7M   117.3M      0K   14.4M   13.6G   50.7%    6.9G    90M  88.2M  71879    +1  138     -
  1.1G   28.5%    335M   117.3M      0K   14.4M   13.6G   50.7%    6.9G    90M  88.2M  71879     -  138     -
  1.1G   63.3%  742.9M   117.3M      0K   14.4M   13.6G   50.7%    6.9G    90M  88.2M  71879     -  138     -
  1.1G   67.5%  791.6M   117.3M      0K   14.4M   13.6G   50.7%    6.9G    90M  88.2M  71879     -  138     -
  1.1G   74.2%  870.4M   117.3M      0K   14.4M   13.6G   50.7%    6.9G    90M  88.2M  71879     -  138     -
  1.1G    3.2%   38.1M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880    +1  138     -
  1.1G    8.9%  104.2M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880     -  138     -
  1.1G   27.9%  327.2M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880     -  138     -
  1.1G   29.5%  346.1M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880     -  138     -
  1.1G   35.2%  413.6M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880     -  138     -
  1.1G   53.5%  628.1M   117.3M   48.6M      0K   13.6G   50.7%    6.9G    90M  88.2M  71880     -  138     -
  1.1G    1.0%   12.1M   117.3M      0K   47.3M   13.6G   50.7%    6.9G    90M  88.2M  71881    +1  138     -
  1.1G   19.2%  225.7M   117.3M      0K   47.3M   13.6G   50.7%    6.9G    90M  88.2M  71881     -  138     -
  1.1G   72.6%  852.3M   117.3M      0K   47.3M   13.6G   50.7%    6.9G    90M  88.2M  71881     -  138     -
  1.1G   79.5%  933.1M   117.3M      0K   47.3M   13.6G   50.7%    6.9G    90M  88.2M  71881     -  138     -
  1.1G    5.6%     66M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882    +1  138     -
  1.1G   36.2%  424.9M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   47.5%  557.2M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   57.0%  669.3M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   66.9%  785.5M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   87.1% 1022.3M   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   99.8%    1.1G   117.3M   53.3M      0K   13.6G   50.7%    6.9G    90M  88.2M  71882     -  138     -
  1.1G   36.7%  430.6M   117.3M      0K     78M   13.6G   50.7%    6.9G    90M  88.2M  71883    +1  138     -
  1.1G   78.1%  915.9M   117.3M      0K     78M   13.6G   50.7%    6.9G    90M  88.2M  71883     -  138     -
  1.1G   19.7%  231.4M   117.3M  117.3M      0K   13.6G   50.9%    6.9G    90M  88.2M  71884    +1  138     -
  1.1G   68.5%  804.3M   117.3M  117.3M      0K   13.6G   50.9%    6.9G    90M  88.2M  71884     -  138     -
  1.1G   87.2% 1022.7M   117.3M  117.3M      0K   13.6G   50.9%    6.9G    90M  88.2M  71884     -  138     -

* Reading gc.log file... done. Scanned 45 lines in 0.0001 seconds.
* Reading the public access log file... done. Scanned 169 lines in 0.0014 seconds.
```

* When it exits, this first section gives useful meta information about the process, information about the requests that are coming into it, GC allocation/promotion rates, and survivor death rates.

```
Meta:
-----
Sample Time:    40 seconds
System Uptime:  1046d18h
CPU Uptime:     25122d21h
Proc Uptime:    6d23h
Proc Usertime:  3d15h (0.01%)
Proc Systime:   8h28m (0.00%)
Proc RSS:       36.95G
Proc VSize:     55.46G
Proc # Threads: 771

YG Allocation Rates*:
---------------------
per sec (min/mean/max):     177.37M/s     214.35M/s     311.97M/s
per day (min/mean/max):      14.61T/d      17.66T/d      25.71T/d

OG Promotion Rates:
-------------------
per sec (min/mean/max):     326.80K/s       6.66M/s      27.12M/s
per hr (min/mean/max):        1.12G/h      23.43G/h      95.33G/h

Survivor Death Rates:
---------------------
Lengths (min/mean/max): 1/1.8/2
Death Rate Breakdown:
   Age 1:  4.6% / 63.9% / 95.2% / 36.1% (min/mean/max/cuml alive %)
   Age 2:  0.0% / 21.3% / 79.2% / 28.4% (min/mean/max/cuml alive %)

GC Information:
---------------
YGC/FGC Count: 8/0 (Rate: 12.00/min, 0.00/min)

GC Load (since JVM start): 0.39%
Sample Period GC Load:     0.51%

CMS Sweep Times: 0.000s /  0.000s /  0.000s / 0.00 (min/mean/max/stdev)
YGC Times:       17ms / 26ms / 51ms / 11.04 (min/mean/max/stdev)
FGC Times:       0ms / 0ms / 0ms / 0.00 (min/mean/max/stdev)
Agg. YGC Time:   205ms
Agg. FGC Time:   0ms

Est. Time Between FGCs (min/mean/max):         12h8m     34m53s      8m34s
Est. OG Size for 1 FGC/hr (min/mean/max):      1.12G     23.43G     95.33G

Overall JVM Efficiency Score*: 99.488%

Current JVM Configuration:
--------------------------
          NewSize: 1.38G
          OldSize: 13.62G
    SurvivorRatio: 10
 MinHeapFreeRatio: 40
 MaxMetaspaceSize: 16E
 MaxHeapFreeRatio: 70
      MaxHeapSize: 15G
    MetaspaceSize: 20.80M
         NewRatio: 2
```

* This section provides what analysis it was able to do. For it to do a very accurate/detailed analysis, you need to let it run long enough to capture at least 3 FGCs. It will warn you if it doesn't have enough data, and will not do analysis in a specific area if there is insufficient data. Here you can see that there wasn't enough FGCs.

```
Recommendation Summary:
-----------------------
Warning: There were only 8 YGC entries to do the analysis on. It's better to
have > 10 to get more realistic results.


* Error: Your survivor age is too short, your last age of 2 has 63.89% of it's
objects still alive. Unset or increase the MaxTenuringThreshold to mitigate this
problem.


---
* The allocation rate is the increase is usage before a GC done. Growth rate
  is the increase in usage after a GC is done.

* The JVM efficiency score is a convenient way to quantify how efficient the
  JVM is. The most efficient JVM is 100% (pretty much impossible to obtain).

* There were no full GCs during this sample period. This reporting will
  be less useful/accurate as a result.

* A copy of the critical data used to generate this report is stored
  in /tmp/jtune_data-{user}.bin.bz2. Please copy this to your homedir if you
  want to save/analyze this further.
```

License
-------
This application is distributed under the terms of the Apache Software License version 2.0. See COPYING file for more details.

Authors
-------
* Eric Bullen <<ebullen@linkedin.com>> (Sr. Site Reliability Engineer at LinkedIn)

FAQ
---
```
Q: Do I have to run jtune.py as root?
A: You should run it as the user of the Java process you want to analyze (or root).

Q: What versions of Java does this support?
A: JTune works with Java versions 6-8.

Q: What JVM options should I have turned on to properly use this tool?
A: You should have the following enabled: -Xloggc, -XX:+PrintTenuringDistribution,
   -XX:+PrintGCDetails, and -XX:+PrintGCDateStamps

Q: Can it tune the G1 GC?
A: Not at this time. G1 is quite a bit harder to tweak, but it's work in progress.
```

