#!/usr/bin/env python2
# -*- coding: utf-8 -*-

"""
@author      Eric Bullen <ebullen@linkedin.com>
@application jtune.py
@version     1.2
@abstract    This tool will give detailed information about the running
             JVM in real-time. It produces useful information that can
             further assist the user in debugging and optimization.
@license     Copyright 2015 LinkedIn Corp. All rights reserved.
             Licensed under the Apache License, Version 2.0 (the
             "License"); you may not use this file except in compliance
             with the License. You may obtain a copy of the License at
             http://www.apache.org/licenses/LICENSE-2.0

             Unless required by applicable law or agreed to in writing,
             software distributed under the License is distributed on an
             "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
             either express or implied.
"""
import atexit
import datetime
import getpass
import locale
import logging
import math
import os
import pickle
import re
import resource
import shlex
import socket
import subprocess as sp
import sys
import textwrap
import time
from decimal import Decimal
from itertools import izip_longest, izip, count
import argparse
import multiprocessing as mp

try:
    locale.setlocale(locale.LC_ALL, 'en_US')
except locale.Error:
    # Try UTF8 variant before failing
    locale.setlocale(locale.LC_ALL, 'en_US.utf8')

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s: "%(name)s" (line: %(lineno)d) - %(levelname)s: %(message)s'))

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# For me to use in PyCharm to read flight recorder files
DEBUG = False


class GCRecord(object):
    """Object definition for a single gc record."""

    _version = "1.0"

    def __init__(self, raw_gc_record=None):
        if raw_gc_record:
            self.raw_gc_record = raw_gc_record
        else:
            self.raw_gc_record = list()

        self.is_cms_gc = False
        self.is_stw_gc = False
        self.cms_sweep_time = None

        self.valid_record = False
        self.record_timestamp = None
        self.jvm_running_time = None
        self.gc_type = None
        self.desired_survivor_size = None
        self.curr_threshold = None
        self.max_threshold = None
        self.ages = list()

        self.young_size_before_gc = None
        self.young_size_after_gc = None
        self.young_size_total = None
        self.young_gc_time = 0

        self.total_heap_before_gc = None
        self.total_heap_after_gc = None
        self.total_heap = None
        self.total_gc_time = 0

        self.og_used = None
        self.stw_time = 0

        self._parse_record()

    def __repr__(self):
        """This prints out the gc record so that it looks as though it came straight from
        the logs."""

        output = list()

        output.append("{0} Runtime: {1} GC Type: {2}".format(self.record_timestamp, self.jvm_running_time, self.gc_type))
        output.append("Desired Survivor Size: {0}, Curr Threshold: {1} (Max: {2})".format(self.desired_survivor_size, self.curr_threshold, self.max_threshold))

        for age in self.ages:
            if age[1] > -1 or age[2] > -1:
                output.append("- Age {0}: {1:>10} bytes, {2:>10} total".format(age[0], age[1], age[2]))

        output.append("YG Before GC: {0}K, YG After GC: {1}K (Total: {2}K), {3} secs".format(self.young_size_before_gc, self.young_size_after_gc, self.young_size_total, self.young_gc_time))
        output.append("Total Heap Before GC: {0}K, Total Heap After GC: {1}K (Total: {2}K), {3} secs".format(self.total_heap_before_gc, self.total_heap_after_gc, self.total_heap, self.total_gc_time))

        return "\n".join(output)

    def _parse_record(self):
        """This loops through record_array to set the class variables that make up the record."""

        self.record_timestamp, record_array = self.raw_gc_record

        #############################################################
        # Capture STW (Full GC, remarks, etc.). Yeah, I could combine
        # these three, but this is good enough for now.
        if any("CMS Initial Mark" in line for line in record_array):
            match = re.search(r", ([\d\.]+) secs\] ", record_array[-1])

            if match:
                self.gc_type = "CMS-STW"
                self.is_stw_gc = True
                self.valid_record = True
                self.stw_time += float(match.group(1))

        if any("CMS Final Remark" in line for line in record_array):
            match = re.search(r", ([\d\.]+) secs\] ", record_array[-1])

            if match:
                self.gc_type = "CMS-STW"
                self.is_stw_gc = True
                self.valid_record = True
                self.stw_time += float(match.group(1))

        if any("Full GC" in line for line in record_array):
            match = re.search(r", ([\d\.]+) secs\] ", record_array[-1])

            if match:
                self.gc_type = "FULL"
                self.is_stw_gc = True
                self.valid_record = True
                self.stw_time += float(match.group(1))

        if not self.is_stw_gc:
            for line in record_array:
                if "CMS-concurrent-sweep: " in line:
                    match = re.match(r"^\d+-\d+-\d+T\d+:\d+:[\d\.]+[+-]\d+: ([\d\.]+): \[CMS-concurrent-sweep: [\d\.]+/([\d\.]+) secs", line)

                    if match:
                        self.is_cms_gc = True
                        self.valid_record = True
                        self.gc_type = "CMS"
                        self.jvm_running_time = float(match.group(1))
                        self.cms_sweep_time = float(match.group(2))

                    break

                if not (self.jvm_running_time or self.gc_type):
                    match = re.match(r"^\d+-\d+-\d+T\d+:\d+:[\d\.]+[+-]\d+: ([\d\.]+): .*\[(\S+)", line)

                    if match:
                        self.jvm_running_time = float(match.group(1))
                        self.gc_type = match.group(2)

                if not (self.desired_survivor_size or self.curr_threshold or self.max_threshold):
                    match = re.match(r"^Desired survivor size (\d+) bytes, new threshold (\d+) \(max (\d+)\)", line)

                    if match:
                        self.valid_record = True

                        self.desired_survivor_size = int(match.group(1))
                        self.curr_threshold = int(match.group(2))
                        self.max_threshold = int(match.group(3))

                        # Here I set the survivor size beforehand, for any that
                        # may be missing as I want all the ages even if they aren't
                        # being used for comparison between GCs
                        for age in range(1, self.max_threshold + 1):
                            self.ages.append((age, -1, -1))

                        continue

                ################################################
                # Skipping records when the JVM has been running
                # for less than 300 seconds
                if self.jvm_running_time < 300:
                    self.valid_record = False
                    break

                #############################
                # Capture survivor ages, etc.
                match = re.match(r"^- age\s+(\d+):\s+(\d+) bytes,\s+(\d+) total", line)

                if match:
                    ############################################################
                    # This while logic block catches any ages that were
                    # fully reaped, and fills them with zeros. This is important
                    # as the analytics needs to know this to determine survivor
                    # death rates/ratios
                    age = int(match.group(1))
                    curr_size = int(match.group(2))
                    max_size = int(match.group(3))

                    self.ages[age - 1] = (age, curr_size, max_size)
                    continue

                ###############################
                # Capture gc reallocation stats
                match = re.match(r"^: (\d+)\w->(\d+)\w\((\d+)\w\), ([\d\.]+) secs\] (\d+)\w->(\d+)\w\((\d+)\w\), ([\d\.]+) secs\]", line)

                if match:
                    self.young_size_before_gc = int(match.group(1)) * 1024
                    self.young_size_after_gc = int(match.group(2)) * 1024
                    self.young_size_total = int(match.group(3)) * 1024
                    self.young_gc_time = Decimal(match.group(4))

                    self.total_heap_before_gc = int(match.group(5)) * 1024
                    self.total_heap_after_gc = int(match.group(6)) * 1024
                    self.total_heap = int(match.group(7)) * 1024
                    self.total_gc_time = Decimal(match.group(8))

                    self.og_used = self.total_heap_after_gc - self.young_size_after_gc


def display(message=None, keep_newline=True, save_output=True):
    """Basically wraps the print function so that it will also save the output to an array for pasting

    Keyword arguments:
    message -- the message to print
    keep_newline -- if this is True, then print it, otherwise, print with no newline (like print with a comma at the end)
    save_output -- if this is false, do not save the output to an array for pasting
    """

    # Not needed (using 'global'), but better to be explicit than not
    global display_output

    if save_output:
        display_output.append(message)

    if message.endswith("\n"):
        message = message[:-1]

    if keep_newline:
        print message
    else:
        print message,


def liverun(cmd=None):
    """Run cmd, and return an iterator of said cmd.

    Keyword arguments:
    cmd -- the command to run
    """
    global subproc

    env = dict(os.environ)

    # Combining stdout and stderr. I can't find a way to keep both separate
    # while getting the data 'live'. itertools.izip_longest seemed like it'd
    # almost do it, but it caches the results before sending it out...
    subproc = sp.Popen(shlex.split(cmd), stdout=sp.PIPE, stderr=sp.STDOUT, env=env)

    return iter(subproc.stdout.readline, b'')


def reduce_seconds(secs=None):
    """Return a compressed representation of time in seconds

    Keyword arguments:
    secs -- a float/int representing the seconds to be 'compressed'
    """

    # The nested  if statements keep it from being too long,
    # by lopping off the non significant values
    retval = ""

    secs = int(float(secs))

    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)

    secs = int("{0:0.0f}".format(secs))

    if days:
        retval += "{0}d".format(days)

    if hours:
        retval += "{0}h".format(hours)

        if days > 0:
            return retval

    if mins:
        retval += "{0}m".format(mins)

        if hours or days:
            return retval

    if secs:
        retval += "{0:}s".format(secs)

    return retval


def sec_diff(first_time=None, second_time=None):
    """Return the number of seconds between two datetime objects

    Keyword arguments:
    first_time  -- The (typically) older time of the two
    second_time -- The (typically) newer time of the two
    """

    time_delta = second_time - first_time
    return time_delta.days * 86400 + time_delta.seconds + Decimal(str(time_delta.microseconds / float(1000000)))


def _min(values=None):
    """A wrapper around the min() function so that it does not error on an empty list"""

    try:
        return min(values)
    except ValueError:
        return 0


def _max(values=None):
    """A wrapper around the max() function so that it does not error on an empty list"""

    try:
        return max(values)
    except ValueError:
        return 0


def median(values=None):
    """Return the median of 'values'

    Keyword arguments:
    values -- the list of numbers
    """

    sorts = sorted(values)
    length = len(sorts)
    result = None

    if not values:
        result = 0
        # raise ValueError, "I can't find the median of an empty list."
    elif not length % 2:
        result = (sorts[(length / 2)] + sorts[(length / 2) - 1]) / 2.0
    else:
        result = sorts[length / 2]

    return result


def mean(values=None, _length=None):
    """Return the mean of 'values'

    Keyword arguments:
    values -- the list of numbers
    _length -- mostly not usable for end-users, needed by the stdev function
    """

    result = None

    if not _length:
        _length = len(values)

    if _length > 0:
        result = Decimal(str(sum(values))) / _length
    else:
        result = 0

    return result


def stdev(values=None):
    """Return the standard deviation of values

    Keyword arguments:
    values -- The poorly named argument that contains the list of numbers
    """

    values_mean = mean(values)
    variance = map(lambda x: math.pow(Decimal(str(x)) - values_mean, 2), values)

    return math.sqrt(mean(variance, len(variance) - 1))


def percentile(values=None, pct=None):
    """Return the percentile of a given values

    Keyword arguments:
    values -- The list of numbers to be analyzed
    pct -- The percentile (can be a float) to be used (100 == 100%, not 1 = 100%, etc.)
    """

    watermark_index = int(round((float(pct) / 100) * len(values) + .5))
    watermark = sorted(values)[watermark_index - 1]

    return [element for element in values if element <= watermark]


def ord_num(number=None):
    return str(number) + ("th" if 4 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th"))


def reduce_k(size=None, precision=2, short_form=True, _place_holder=0):
    """Return a compressed representation of a given number of bytes

    Keyword arguments:
    size -- the size in bytes
    precision -- what precision should be used (places to the right of the decimal)
    short_form -- (true/false). Use 'K' instead of 'KiB', etc.
    """

    if not isinstance(size, Decimal):
        size = Decimal(str(size))

    # You know.. just in case we ever get to a yottabyte
    if short_form:
        iec_scale = ['K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
    else:
        iec_scale = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']

    if not isinstance(size, Decimal):
        size = Decimal(str(size))

    if abs(size) >= 1024:
        _place_holder += 1
        return reduce_k(size / Decimal("1024.0"), precision=precision, short_form=short_form, _place_holder=_place_holder)
    else:
        value = Decimal("{0:.{1}f}".format(size, precision))

        if Decimal(str(int(value))) == value:
            value = int(value)

        if short_form:
            return "{0}{1}".format(value, iec_scale[_place_holder])
        else:
            return "{0} {1}".format(value, iec_scale[_place_holder])


def _run_analysis(gc_data=None, jmap_data=None, jstat_data=None, proc_details=None, replay_file=None, optimized_for_ygcs_rate=None):
    """The meat-and-potatoes of this tool. This takes in numerous data structures,
    and prints out a report of the analysis of them."""

    # Formulas to get the JVM configuration just from JMap:
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # eden_size == (newsize * survivor_ratio)/(2 + survivor_ratio)
    # survivor_size == eden_size * (1/survivor_ratio)
    # og_size == max_heap_size - eden_size - survivor_size
    # og_used == heap_used - eden_used

    if not gc_data:
        logger.error("I can't do any analysis for this sample period because there wasn't enough data in the GC log. Exiting.")
        sys.exit(1)

    ############################################################
    # Get some summary data that doesn't require GC log analysis
    textwrap_offset = 80

    # Loop through the GC data array to find all CMS events, and capture
    # how long they took.
    cms_times = [record.cms_sweep_time for record in gc_data if record.is_cms_gc]

    display("\n")
    display("Meta:\n")
    display("~~~~~\n")

    sample_time_secs = sec_diff(gc_data[0].record_timestamp, gc_data[-1].record_timestamp)

    if sample_time_secs < 60:
        display("GC Sample Time: {0} seconds\n".format(sample_time_secs))
    else:
        display("GC Sample Time: {0} ({1} seconds)\n".format(reduce_seconds(sample_time_secs), sample_time_secs))

    display("GC Sample Time from {0} to {1}\n".format(gc_data[0].record_timestamp, gc_data[-1].record_timestamp))

    if proc_details:
        cpu_count = mp.cpu_count()
        cpu_uptime = cpu_count * proc_details['sys_uptime_seconds']
        proc_utime_pct = proc_details['proc_utime_seconds'] / cpu_uptime
        proc_stime_pct = proc_details['proc_stime_seconds'] / cpu_uptime

        display("System Uptime:  {0}\n".format(reduce_seconds(proc_details['sys_uptime_seconds'])))
        display("Proc Uptime:    {0}\n".format(reduce_seconds(proc_details['proc_uptime_seconds'])))
        display("Proc Usertime:  {0} ({1:0.2%})\n".format(reduce_seconds(proc_details['proc_utime_seconds']), proc_utime_pct))
        display("Proc Systime:   {0} ({1:0.2%})\n".format(reduce_seconds(proc_details['proc_stime_seconds']), proc_stime_pct))
        display("Proc RSS:       {0}\n".format(reduce_k(proc_details['proc_rss_bytes'] / 1024)))
        display("Proc VSize:     {0}\n".format(reduce_k(proc_details['proc_vsize_bytes'] / 1024)))
        display("Proc # Threads: {0}\n".format(proc_details['num_threads']))

    display("\n")

    # Exit out as I don't have enough gc_data to do any analysis on
    if len(gc_data) < 2:
        display("\n")
        display("* NOTE: There wasn't enough data to do any analysis. Please let the tool\n")
        display("        gather at least 2 complete gc.log records (found {0}).\n".format(len(gc_data)))

        return False

    survivor_info = dict()
    young_gc_count_delta = len([record.is_stw_gc for record in gc_data if not record.is_stw_gc])
    full_gc_count_delta = len([record.is_stw_gc for record in gc_data if record.is_stw_gc])
    jvm_uptime = gc_data[-1].jvm_running_time
    sample_gc_time = sum(record.total_gc_time for record in gc_data)
    sample_gc_load = (sample_gc_time / Decimal(str(sample_time_secs))) * 100

    #######################################################
    # Get young gen allocation rates over the sample period
    yg_rates = list()
    for first_gc, second_gc in zip(gc_data, gc_data[1:]):
        if any([second_gc.is_stw_gc, first_gc.is_stw_gc, first_gc.is_cms_gc, second_gc.is_cms_gc]):
            continue

        # Iterate over the gc logs 2 at a time
        # [1, 2, 3, 4] ->
        # [(1, 2), (2, 3), (3, 4)]
        #
        time_delta = sec_diff(first_gc.record_timestamp, second_gc.record_timestamp)

        try:
            yg_size_delta = second_gc.young_size_before_gc - first_gc.young_size_after_gc
            yg_growth_delta = second_gc.young_size_after_gc - first_gc.young_size_after_gc
        except TypeError:
            display("\n".join(textwrap.wrap("Warning: Something is really wrong with this JVM; I couldn't get correct GC data for it.", textwrap_offset)))
            display("")

            yg_size_delta = 0
            yg_growth_delta = 0

        # These are in KiB/s
        yg_alloc_rate = yg_size_delta / time_delta
        yg_growth_rate = yg_growth_delta / time_delta

        yg_rates.append((yg_alloc_rate, yg_growth_rate))

    #####################################################
    # Get old gen promotion rates over the sample period
    og_rates = list()
    for first_gc, second_gc in zip(gc_data, gc_data[1:]):
        if any([second_gc.is_stw_gc, first_gc.is_stw_gc, first_gc.is_cms_gc, second_gc.is_cms_gc]):
            continue

        time_delta = sec_diff(first_gc.record_timestamp, second_gc.record_timestamp)

        # These are in KiB/s
        og_allocation_delta = (second_gc.og_used - first_gc.og_used) / Decimal("1024")
        og_allocation_rate = og_allocation_delta / time_delta

        ############################################################################
        # I only want when the old gen is growing. If it's decreasing, it's probably
        # b/c there was a FGC, and space is being reclaimed.
        if og_allocation_delta > 0:
            # This is in KiB/s
            og_rates.append(og_allocation_rate)

    ############################
    # Calc survivor death ratios
    gc_survivor_death_rates = list()

    for first_gc, second_gc in zip(gc_data, gc_data[1:]):
        if any([second_gc.is_stw_gc, first_gc.is_stw_gc, first_gc.is_cms_gc, second_gc.is_cms_gc]):
            continue

        survivor_death_rates = list()

        for first_age, second_age in zip(first_gc.ages, second_gc.ages[1:]):
            # The second age CAN be bigger than the first age. I verified
            # this in the gc.logs (still not sure how/why)

            # ID 0 is the age number
            # ID 1 is bytes in that age
            # ID 2 is the total bytes for that age
            if second_age[1] == -1:
                # I don't think I want to capture any changes if
                # the survivor space didn't exist (-1 as a default value- see above)
                continue
                # survivor_death_rates.append(Decimal(0))
            else:
                survivor_death_rates.append(1 - (Decimal(second_age[1]) / first_age[1]))

        gc_survivor_death_rates.append(survivor_death_rates)


    #################################################################################
    # Since I have 2 in-scope valid GCs, I'm going to calculate some needed JVM sizes
    # the sizes will be fixed if I have a fixed heap size (which we do in prod)
    jvm_mem_cfg = dict()

    try:
        jvm_mem_cfg["og_size"] = (first_gc.total_heap - first_gc.young_size_total) * 1024
    except TypeError:
        display("\n".join(textwrap.wrap("Error: I could not find a non CMS/FGC GC record for analysis. Exiting.", textwrap_offset)))
        display("")
        sys.exit(1)

    jvm_mem_cfg["survivor_size"] = (first_gc.desired_survivor_size * 2)
    jvm_mem_cfg["eden_size"] = (first_gc.young_size_total * 1024) - jvm_mem_cfg["survivor_size"]
    jvm_mem_cfg["total_heap"] = (first_gc.total_heap * 1024) + jvm_mem_cfg["survivor_size"]
    jvm_mem_cfg["new_size"] = (jvm_mem_cfg["eden_size"] + (jvm_mem_cfg["survivor_size"] * 2))

    #########################################################
    # Now that I have a crap-ton of curated data, report out.
    # This grabs the first part of the tuple (which is
    # the total allocation for that gc (not growth!)
    yg_alloc_rates = [entry[0] for entry in yg_rates]
    min_yg_rate, mean_yg_rate, max_yg_rate = _min(yg_alloc_rates), mean(yg_alloc_rates), _max(yg_alloc_rates)

    display("YG Allocation Rates*:\n")
    display("~~~~~~~~~~~~~~~~~~~~~\n")
    display("per sec (min/mean/max): {0:>13} {1:>13} {2:>13}\n".format(reduce_k(min_yg_rate) + "/s", reduce_k(mean_yg_rate) + "/s", reduce_k(max_yg_rate) + "/s"))
    display("per hr  (min/mean/max): {0:>13} {1:>13} {2:>13}\n".format(reduce_k(min_yg_rate * 3600) + "/h", reduce_k(mean_yg_rate * 3600) + "/h", reduce_k(max_yg_rate * 3600) + "/h"))
    display("\n")

    # This grabs the second part of the tuple (which is
    # the total growth for that gc (not allocation rate!)
    min_og_rate, mean_og_rate, max_og_rate = _min(og_rates), mean(og_rates), _max(og_rates)

    display("OG Promotion Rates:\n")
    display("~~~~~~~~~~~~~~~~~~~\n")
    display("per sec (min/mean/max): {0:>13} {1:>13} {2:>13}\n".format(reduce_k(min_og_rate) + "/s", reduce_k(mean_og_rate) + "/s", reduce_k(max_og_rate) + "/s"))
    display("per hr  (min/mean/max): {0:>13} {1:>13} {2:>13}\n".format(reduce_k(min_og_rate * 3600) + "/h", reduce_k(mean_og_rate * 3600) + "/h", reduce_k(max_og_rate * 3600) + "/h"))
    display("\n")

    ################################################
    # Survivor Lengths- wanted to make a nested list
    # comprehension, but I suppose that's a bit ugly
    # to debug/read

    display("Survivor Death Rates:\n")
    display("~~~~~~~~~~~~~~~~~~~~~\n")

    survivor_lengths = list()
    for sub_arr in gc_survivor_death_rates:
        survivor_lengths.append(len([elem for elem in sub_arr if elem > 0]))

    display("Lengths (min/mean/max): {0}/{1:0.1f}/{2}\n".format(_min(survivor_lengths), mean(survivor_lengths), _max(survivor_lengths)))
    display("Death Rate Breakdown:\n")

    cuml_pct = 1
    death_ages = list()
    for survivor_num, pct_list in enumerate(izip_longest(*gc_survivor_death_rates, fillvalue=0), 1):
        min_pct = min(pct_list)
        mean_pct = mean(pct_list)
        max_pct = max(pct_list)
        cuml_pct *= 1 - mean_pct

        death_ages.append(mean_pct)

        survivor_info[survivor_num] = min_pct, mean_pct, max_pct

        display("   Age {0}: {1:>5} / {2:>5} / {3:>5} / {4:>5} (min/mean/max/cuml alive %)\n".format(survivor_num, "{0:0.1%}".format(min_pct), "{0:0.1%}".format(mean_pct), "{0:0.1%}".format(max_pct),
                                                                                                     "{0:0.1%}".format(cuml_pct)))

    ##################################
    # GC Times
    young_gc_times = [record.young_gc_time * 1000 for record in gc_data if not record.is_stw_gc]
    full_gc_times = [record.stw_time * 1000 for record in gc_data if record.is_stw_gc]

    if sample_time_secs:
        if young_gc_count_delta:
            ygc_rate = (young_gc_count_delta / sample_time_secs) * 60
        else:
            ygc_rate = 0

        if full_gc_count_delta:
            fgc_rate = (full_gc_count_delta / sample_time_secs) * 60
        else:
            fgc_rate = 0

    display("\n")
    display("GC Information:\n")
    display("~~~~~~~~~~~~~~~\n")
    display("YGC/FGC Count: {0}/{1} (Rate: {2:0.2f}/min, {3:0.2f}/min)\n".format(young_gc_count_delta, full_gc_count_delta, ygc_rate, fgc_rate))
    display("\n")
    display("Sample Period GC Load:     {0:0.2f}%\n".format(sample_gc_load))
    display("")

    display("CMS Sweep Times: {0:0.3f}s /  {1:0.3f}s /  {2:0.3f}s / {3:0.2f} (min/mean/max/stdev)\n".format(_min(cms_times), mean(cms_times), _max(cms_times), stdev(cms_times)))
    display("YGC Times:       {0:0.0f}ms / {1:0.0f}ms / {2:0.0f}ms / {3:0.2f} (min/mean/max/stdev)\n".format(_min(young_gc_times), mean(young_gc_times), _max(young_gc_times), stdev(young_gc_times)))
    display("FGC Times:       {0:0.0f}ms / {1:0.0f}ms / {2:0.0f}ms / {3:0.2f} (min/mean/max/stdev)\n".format(_min(full_gc_times), mean(full_gc_times), _max(full_gc_times), stdev(full_gc_times)))

    agg_ygc_time = sum(young_gc_times)
    agg_fgc_time = sum(full_gc_times)

    display("Agg. YGC Time:   {0:0.0f}ms\n".format(agg_ygc_time))
    display("Agg. FGC Time:   {0:0.0f}ms\n".format(agg_fgc_time))
    display("\n")

    if og_rates:
        display(
            "Est. Time Between FGCs (min/mean/max):    {0:>10} {1:>10} {2:>10}\n".format(reduce_seconds(jvm_mem_cfg["og_size"] / min_og_rate), reduce_seconds(jvm_mem_cfg["og_size"] / mean_og_rate),
                                                                                         reduce_seconds(jvm_mem_cfg["og_size"] / max_og_rate)))
    else:
        display("Est. Time Between FGCs (min/mean/max):    {0:>10} {1:>10} {2:>10}\n".format("n/a", "n/a", "n/a"))

    display("Est. OG Size for 1 FGC/hr (min/mean/max): {0:>10} {1:>10} {2:>10}\n".format(reduce_k(min_og_rate * 3600), reduce_k(mean_og_rate * 3600), reduce_k(max_og_rate * 3600)))
    display("\n")

    display("Overall JVM Efficiency Score*: {0:0.3f}%\n".format(100 - sample_gc_load))
    display("\n")

    ###################################
    # JMap Data
    display("Current JVM Mem Configuration:\n")
    display("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n")

    if jmap_data:
        for k, v in jmap_data.iteritems():
            if "Size" in k:
                v = reduce_k(v / 1024)

            display("{0:>17}: {1}\n".format(k, v))
    else:
        for k, v in jvm_mem_cfg.iteritems():
            display("{0:>17}: {1}\n".format(k, reduce_k(v / 1024)))

    display("\n")

    ######################
    # Show recommendations
    _show_recommendations(death_ages, young_gc_times, full_gc_times, fgc_rate, ygc_rate, yg_alloc_rates, og_rates, jvm_mem_cfg, jmap_data, jstat_data, gc_data, cms_times, survivor_info,
                          optimized_for_ygcs_rate, proc_details)

    display("~~~\n")
    display("* The allocation rate is the increase in usage before a GC done. Growth rate\n")
    display("  is the increase in usage after a GC is done.\n")

    display("\n")
    display("* The JVM efficiency score is a convenient way to quantify how efficient the\n")
    display("  JVM is. The most efficient JVM is 100% (pretty much impossible to obtain).\n")

    if full_gc_count_delta == 0:
        display("\n")
        display("* There were no full GCs during this sample period. This reporting will\n")
        display("  be less useful/accurate as a result.\n")

    display("\n")
    display("* A copy of the critical data used to generate this report is stored\n")
    display("  in /tmp/jtune_data-{0}.bin.bz2. Please copy this to your homedir if you\n".format(user))
    display("  want to save/analyze this further.\n")


def _get_survivor_info(death_ages=None, survivor_info=None, gc_data=None, survivor_problem_pct=None, curr_ng_size=None, adj_ng_size=None):
    """This looks at the survivor info data structure, and will return the max
    tenuring size, and max tenuring age that it feels is needed."""

    # This is roughly how much larger the survivor space should be to counteract the increase
    # in the frequency of ygcs caused from the smaller NG size as it pushes data into the
    # survivor space more often. I don't need to change the MaxTenuringThreshold as that is
    # mostly constant depending on how data ages.
    #
    # I'm adjusting the size of the survivor space based on the eden change. It MAY be better
    # adjusting this based on time of how frequent the ygcs are happening.
    ng_size_delta = curr_ng_size - adj_ng_size

    # Going to use this to change the maxtenuringtrheshold parameter. The reason is that
    # ygcs will happen less/more often if I change the ng size, and I'll need to counter
    # that by increasing/decreasing the tenuring threshold to keep things in balance.
    ng_size_delta_pct = adj_ng_size / curr_ng_size

    # Changing the 'survivor_problem_pct' which is the watermark
    # for objects still alive. If it's over that amount, then the
    # tenuring threshold needs to be increased, if it's less, then
    # the age is good. HOWEVER, I use death rate, so[-1] a 85% death
    # rate is a 15% survivor rate.
    survivor_watermark = 100 - survivor_problem_pct

    # Get the max survivor age allowed per the jvm configuration
    max_survivor_age = gc_data[0].max_threshold

    # The survivor_info structure is the decrease in size for that
    # age going into the next, so if the max here is 6, the actual max
    # survivor size used is 7.
    longest_used_ratio = len(survivor_info) + 1

    # Survivor percentage of surviving objects
    age_objects_still_alive = list()
    current_percentage = 100

    for key in sorted(survivor_info):
        # [1] is the average, [2] is the max
        mean_death_rate_pct = survivor_info[key][1]
        current_percentage *= 1 - mean_death_rate_pct

        age_objects_still_alive.append(current_percentage)

    error_msg = None

    if max_survivor_age < 15:
        if longest_used_ratio == max_survivor_age:
            if age_objects_still_alive[-1] > ((100 - survivor_watermark) / 100.0):
                error_msg = "The survivor ratio of {0} is too small as {1:0.1f}% of the objects are still alive. Try increasing the MaxTenuringThreshold (Max: 15) parameter, and running this analysis again.".format(
                    longest_used_ratio, age_objects_still_alive[-1])
            elif not survivor_info:
                error_msg = "For the examined sample period, I could not retrieve any meaningful survivor statistics from the gc.log. This JVM is either sick, or the sample period was too short."
        elif not survivor_info:
            error_msg = "For the examined sample period, I could not retrieve any meaningful survivor statistics from the gc.log. This JVM is either sick, or the sample period was too short."
    elif not survivor_info:
        error_msg = "For the examined sample period, I could not retrieve any meaningful survivor statistics from the gc.log. This JVM is either sick, or the sample period was too short."

    if error_msg:
        raise ValueError(error_msg)

    ###########################################################
    # Don't confuse the 'min()' with the 'max' variable. I want
    # the first age where it's less than survivor_problem_pct
    try:
        max_tenuring_age = min([k for k, v in enumerate(age_objects_still_alive, 1) if v <= survivor_problem_pct])
    except ValueError:
        max_tenuring_age = 0

    if not max_tenuring_age:
        # Not sure if I like this algorithm, but it seems close enough
        below_threshold_ct = len([death_pct for death_pct in death_ages if death_pct <= Decimal(".04")])
        below_threshold_pct = below_threshold_ct / float(len(death_ages))

        # If more than 33% of the ages are at or below 4%, make a note of it.
        if below_threshold_pct > .33:
            # It's speculative that I should add to the heap any objects that aren't reaped
            # after cutting off the MaxTenuringThrehold, but since it's not getting reaped anyway,
            # it may not change anything, so not adjusting for the time being.

            # We're using all the available ages, but objects are still alive...
            if max_survivor_age == len(death_ages):
                display("\n".join(textwrap.wrap(
                    "* Warning: It looks like your tenuring threshold is too high - {0:0.0%} of your ages are reaping at or below 4% of the objects. We could make it easier for the JVM if we reduce your MaxTenuringThreshold by {1} to {2} instead of {3}.".format(
                        below_threshold_pct, below_threshold_ct, len(death_ages) - below_threshold_ct, max_survivor_age))))
            else:
                display("\n".join(textwrap.wrap(
                    "* Warning: It looks like your tenuring threshold is too high - {0:0.0%} of your ages are reaping at or below 4% of the objects. We could make it easier for the JVM if we reduce your MaxTenuringThreshold by {1} to {2} instead of {3}. BE CAREFUL - your max *used* age in the gc.logs of {4} is less than the configured max age of {3} - make sure that you used a large enough sample size, and let the JVM go through 3 FGCs (option: '-s 3') and is being checked during peak traffic.".format(
                        below_threshold_pct, below_threshold_ct, len(death_ages) - below_threshold_ct, max_survivor_age, len(death_ages)))))

            max_tenuring_age = len(death_ages) - below_threshold_ct
        else:
            display("\n".join(textwrap.wrap(
                "* Warning: Your survivor age is too short, your last age of {0} has {1:0.2f}% of its objects still alive. Because of this, I'm unable to reliably determine how your objects are aging. Unset or increase the MaxTenuringThreshold (max: 15) to mitigate this problem.".format(
                    len(age_objects_still_alive), age_objects_still_alive[-1]))))

    tenure_sizes = list()
    for gc_record in gc_data:
        try:
            tenure_sizes.append(gc_record.ages[max_tenuring_age - 1][2])
        except IndexError:
            # I saw a gc record that doesn't have that age
            # level, so skip it.
            pass

    # It's recommended to have the tenuring size 2x the max tenure size, I then
    # add in the change in newgen (ng_size_delta) to offset the decrease/increase
    # in newgen as calculated in this parent's function. The 'ng_size_delta / 2' is
    # such that I increase the whole max_tenuring_size by ng_size_delta, but since
    # there are two survivor spaces, I need to split the ng_size_delta by 2 for each
    # survivor space
    max_tenuring_size = (max(tenure_sizes) * 2) + (ng_size_delta / 2)
    survivor_ratio = adj_ng_size / max_tenuring_size

    # Checking if survivor space is LARGER than the newgen size
    if survivor_ratio < 1:
        display("\n".join(textwrap.wrap(
            "* Warning: The calculated recommended survivor ratio of {0:0.2f} is less than 1. This is not possible, so I increased the size of newgen by {1}, and set the survivor ratio to 1. Try the tuning suggestions, and watch closely.\n".format(
                survivor_ratio, reduce_k((max_tenuring_size - adj_ng_size) / 1024)), textwrap_offset)) + "\n\n")

        # This is close, but still wrong. If I run into this condition, then I need to
        # also fix the newgen size b/c the tenured size is based off of the newgen
        # size before I knew there was an issue. I think this is probably close enough
        # for now.
        survivor_ratio = 1
        adj_ng_size = max_tenuring_size
    else:
        adj_ng_size += max_tenuring_size

    # Now, change the max tenuring age/threshold
    max_tenuring_age *= (1 / ng_size_delta_pct)

    return adj_ng_size, survivor_ratio, max_tenuring_size, max_tenuring_age


def _show_recommendations(death_ages=None, young_gc_times=None, full_gc_times=None, fgc_rate=None, ygc_rate=None, yg_alloc_rates=None, og_rates=None, jvm_mem_cfg=None, jmap_data=None, jstat_data=None,
                          gc_data=None, cms_times=None, survivor_info=None, optimized_for_ygcs_rate=None, proc_details=None):
    """This is where any jvm tuning recommendations happens."""

    ###########################################################################
    # The basis of these recommendations are as follows:
    #
    # 1) More frequent YGCs which take less time is almost always better
    #    than less frequent YGCs, but taking longer; consistently slow is
    #    better than periodically slower
    # 2) YGC times should have a low standard deviation(<= 5)
    # 3) YGC times should be low (<= 50ms, ideally)

    display("Recommendation Summary:\n")
    display("~~~~~~~~~~~~~~~~~~~~~~~\n")

    # This is how many ygcs/sec should be happening, if the mean ygc
    # times are higher than desired
    ygc_time_goal_ms = 50
    ygc_stdev_goal = 5

    # YGC mean ms percentile - lop off the worst offenders
    # I am changing it instead of a mean of the 99p, doing a
    # max of the 75p; may be better
    ygc_pctile = 75

    # This is just for analysis purposes; need a decent sample set count
    ygc_count_goal = 10
    fgc_count_goal = 3

    # Marker for indicating if current config is good for
    # the Java G1 garbage collector
    ready_for_g1 = False

    survivor_problem_pct = 10

    ygc_stdev = stdev(percentile(young_gc_times, ygc_pctile))
    ygc_mean_ms = float(max(percentile(young_gc_times, ygc_pctile)))

    if jmap_data:
        curr_ng_size = jmap_data['NewSize']
        curr_og_size = jmap_data['OldSize']

        # Not using b/c this data is not in the GC logs (and
        # really doesn't need to be tuned...
        # if "PermSize" in jmap_data:
        #    curr_pg_ms_size = jmap_data['PermSize']
        # else:
        #    curr_pg_ms_size = jmap_data['MetaspaceSize']

        max_heap_size = jmap_data['MaxHeapSize']
    else:
        curr_ng_size = jvm_mem_cfg["new_size"]
        curr_og_size = jvm_mem_cfg["og_size"]
        max_heap_size = jvm_mem_cfg["total_heap"]

    adj_ng_size = curr_ng_size

    #########################################################################################################
    # This is an estimate. Because we use CMS for FGCs, it's an iterative process, and while the CMS reset is
    # happening, more objects are being tenured into OG. The best we can do (I think) is to find the minimum
    # size of OU, and go from there. This is why it's super important to have more than 2 FGCs to look at.
    #

    # This is tricky. I need to find the first record where the previous og size is bigger than
    # the current. This identifies when the first CMS runs, and from there, I can find the minimum

    normal_gc_data = filter(lambda x: x.og_used > 0, gc_data)

    try:
        record_num = [record_num for record_num, first_gc, second_gc in izip(count(), normal_gc_data, normal_gc_data[1:]) if first_gc.og_used > second_gc.og_used][0]
    except IndexError:
        live_data_size_bytes = None
    else:
        live_data_size_bytes = _min(record.og_used for record in normal_gc_data[record_num:])

    if proc_details and proc_details['proc_uptime_seconds'] < 300:
        display("\n".join(textwrap.wrap(
            "Warning: The process I'm doing the analysis on has been up for {0}, and may not be in a steady-state. It's best to let it be up for more than 5 minutes to get more realistic results.\n".format(
                reduce_seconds(proc_details['proc_uptime_seconds'])))) + "\n\n")

    #################################################
    # Find the recommended NewGen size
    if len(young_gc_times) < ygc_count_goal:
        display("\n".join(
            textwrap.wrap("Warning: There were only {0} YGC entries to do the analysis on. It's better to have > {1} to get more realistic results.\n".format(len(young_gc_times), ygc_count_goal),
                          textwrap_offset)) + "\n\n")

    if ygc_stdev > ygc_stdev_goal * 4:
        comment = "VERY inconsistent"
    elif ygc_stdev > ygc_stdev_goal * 2:
        comment = "pretty inconsistent"
    elif ygc_stdev > ygc_stdev_goal:
        comment = "somewhat consistent"
        ready_for_g1 = True
    else:
        comment = "very consistent"
        ready_for_g1 = True

    messages = list()

    # This logic block goes through different optimizaion scenarios that it
    # uses to find an optimal setting.
    # messages.append("- The mean YGC rate is {0:0.2f}/min, and the max {1} percentile YGC time is {2:0.0f}ms (stdev of {3:0.2f} which is {4}). It's best to have the mean YGC time be at or below {5}ms, and the YGC stdev at or below {6} if possible.".format(ygc_rate, ord_num(ygc_pctile), ygc_mean_ms, ygc_stdev, comment, ygc_time_goal_ms, ygc_stdev_goal))

    # TODO: Too much repetition in this code block
    if (optimized_for_ygcs_rate > ygc_rate) and (ygc_stdev > ygc_stdev_goal or ygc_mean_ms > ygc_time_goal_ms):
        adj_ng_size = curr_ng_size * (ygc_rate / optimized_for_ygcs_rate)

        ######################################################################
        # Figure out Tenuring Threshold & size for the survivor spaces, basing
        # it on the last age where below 10% still live
        try:
            new_adj_ng_size, survivor_ratio, max_tenuring_size, max_tenuring_age = _get_survivor_info(death_ages, survivor_info, gc_data, survivor_problem_pct, curr_ng_size, adj_ng_size)

            # Go ahead and set it regardless
            adj_ng_size = new_adj_ng_size
        except ValueError as msg:
            display("\n" + "\n".join(textwrap.wrap("* Error: {0}".format(msg), textwrap_offset)) + "\n\n")
            display("")
            return False

        messages.append(
            "- With a mean YGC time goal of {0:0.0f}ms, the suggested (optimized for a YGC rate of {1:0.2f}/min) size of NewGen (including adjusting for calculated max tenuring size) considering the above criteria should be {2:0.0f} MiB (currently: {3:0.0f} MiB).".format(
                ygc_time_goal_ms, optimized_for_ygcs_rate, float(adj_ng_size) / 1024.0 / 1024.0, float(curr_ng_size) / 1024.0 / 1024.0))

        if new_adj_ng_size < curr_ng_size:
            messages.append(
                "- Because we're decreasing the size of NewGen, it can have an impact on system load due to increased memory management requirements. There's not an easy way to predict the impact to the application, so watch this after it's tuned.")

    elif ygc_mean_ms > ygc_time_goal_ms:
        adj_ng_size = curr_ng_size * (ygc_time_goal_ms / ygc_mean_ms)

        ######################################################################
        # Figure out Tenuring Threshold & size for the survivor spaces, basing
        # it on the last age where below 10% still live
        try:
            new_adj_ng_size, survivor_ratio, max_tenuring_size, max_tenuring_age = _get_survivor_info(death_ages, survivor_info, gc_data, survivor_problem_pct, curr_ng_size, adj_ng_size)

            # Go ahead and set it regardless
            adj_ng_size = new_adj_ng_size
        except ValueError as msg:
            display("\n" + "\n".join(textwrap.wrap("* Error: {0}".format(msg), textwrap_offset)) + "\n\n")
            display("")
            return False

        messages.append(
            "- With a mean YGC time goal of {0:0.0f}ms, the suggested (optimized for YGC time) size of NewGen (including adjusting for calculated max tenuring size) considering the above criteria should be {1:0.0f} MiB (currently: {2:0.0f} MiB).".format(
                ygc_time_goal_ms, float(adj_ng_size) / 1024.0 / 1024.0, float(curr_ng_size) / 1024.0 / 1024.0))

        if new_adj_ng_size < curr_ng_size:
            messages.append(
                "- Because we're decreasing the size of NewGen, it can have an impact on system load due to increased memory management requirements. There's not an easy way to predict the impact to the application, so watch this after it's tuned.")
    else:
        adj_ng_size = curr_ng_size

        ######################################################################
        # Figure out Tenuring Threshold & size for the survivor spaces, basing
        # it on the last age where below 10% still alive
        try:
            new_adj_ng_size, survivor_ratio, max_tenuring_size, max_tenuring_age = _get_survivor_info(death_ages, survivor_info, gc_data, survivor_problem_pct, curr_ng_size, adj_ng_size)

            # Go ahead and set it regardless
            adj_ng_size = new_adj_ng_size
        except ValueError as msg:
            display("\n" + "\n".join(textwrap.wrap("* Error: {0}".format(msg), textwrap_offset)) + "\n\n")
            display("")
            return False

        messages.append("- The mean YGC rate is {0:0.2f}/min, and the mean YGC time is {1:0.0f}ms (stdev of {2:0.2f} which is {3}).".format(ygc_rate, ygc_mean_ms, ygc_stdev, comment))

    for message in messages:
        display("\n".join(textwrap.wrap(message)) + "\n")

    #################################################
    # Find the recommended PermGen size
    #
    # Removing this block b/c permgen/metaspace usage isn't in the gc.logs
    #

    ############################################
    # Find out what the survivor ratio should be
    display("\n".join(
        textwrap.wrap("- Looking at the worst (max) survivor percentages for all the ages, it looks like a TenuringThreshold of {0:0.0f} is ideal.".format(max_tenuring_age), textwrap_offset)) + "\n")
    display("\n".join(textwrap.wrap(
        "- The survivor size should be 2x the max size for tenuring threshold of {0:0.0f} given above. Given this, the survivor size of {1:0.0f}M is ideal.".format(max_tenuring_age,
                                                                                                                                                                    max_tenuring_size / 1024 / 1024,
                                                                                                                                                                    textwrap_offset))) + "\n")
    display("\n".join(textwrap.wrap("- To ensure enough survivor space is allocated, a survivor ratio of {0:0.0f} should be used.".format(survivor_ratio), textwrap_offset)) + "\n")

    #################################################
    # Find the recommended max heap size
    if len(full_gc_times) < fgc_count_goal:
        display("\n" + "\n".join(textwrap.wrap(
            "* Error: You really need to have at least {0} (preferably more) FGCs happen (I found {1}) before doing any OG size recommendation analysis. Stopping any further analysis.\n".format(
                fgc_count_goal, len(full_gc_times)), textwrap_offset)) + "\n\n")
        display("\n")
        return False

    recommended_max_heap_size = 3.5 * float(live_data_size_bytes) + float(max_tenuring_size + adj_ng_size)
    if max_heap_size != recommended_max_heap_size:
        display("\n".join(textwrap.wrap(
            "- It's recommended to have the max heap size 3-4x the size of the live data size (OldGen + PermGen), and adjusted to include the recommended survivor and newgen size. New recommended size is {0:0.0f}MiB (currently: {1:0.0f}MiB).".format(
                float(recommended_max_heap_size) / 1024.0 / 1024.0, float(max_heap_size) / 1024.0 / 1024.0), textwrap_offset)) + "\n")

    #################################################
    # Figure out the occupancy fraction
    max_cms_time = float(_max(cms_times))
    # Not doing the MAX, but a max of a percentile of the og rates- I think that's better
    # maybe doing a mean of a percentile?
    pct_number = 99

    # KiB -> B
    max_og_rate = float(_max(percentile(og_rates, pct_number))) * 1024
    oldgen_offset = curr_og_size - (float(_max(yg_alloc_rates) / 1024) * max_cms_time) - (max_cms_time * max_og_rate)
    occ_fraction = math.floor((float(oldgen_offset) / curr_og_size) * 100)

    display("\n".join(textwrap.wrap(
        "- With a max {0} percentile OG promotion rate of {1}/s, and the max CMS sweep time of {2}s, you should not have a occupancy fraction any higher than {3:0.0f}.".format(ord_num(pct_number),
                                                                                                                                                                                reduce_k(Decimal(str(
                                                                                                                                                                                    max_og_rate / 1024.0))),
                                                                                                                                                                                max_cms_time,
                                                                                                                                                                                occ_fraction),
        textwrap_offset)) + "\n")

    # Java 7 G1 Stuff
    display("\n")
    display("Java G1 Settings:\n")
    display("~~~~~~~~~~~~~~~~~~~\n")
    if ready_for_g1:
        display("\n".join(textwrap.wrap(
            "- With a max ygc stdev of {0:0.2f}, and a {1} percentile ygc mean ms of {2:0.0f}ms, your config is good enough to move to the G1 garbage collector.".format(ygc_stdev, ord_num(pct_number),
                                                                                                                                                                         ygc_mean_ms),
            textwrap_offset)) + "\n")
        display("\n".join(textwrap.wrap("- Since G1 uses one space for everything, the consolidated heap size should be {0:0.0f}MiB.".format(float(recommended_max_heap_size) / 1024.0 / 1024.0),
                                        textwrap_offset)) + "\n")
    else:
        display("\n".join(textwrap.wrap(
            "- With a max ygc stdev of {0:0.2f}, and a {1} percentile ygc mean ms of {2:0.0f}ms, your config is probably not ready to move to the G1 garbage collector. Try tuning the JVM, and see if that improves things first.".format(
                ygc_stdev, ord_num(pct_number), ygc_mean_ms), textwrap_offset)) + "\n")

    display("\n")
    display("The JVM arguments from the above recommendations:\n")
    display("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n")
    display("\n".join(textwrap.wrap("-Xmx{0:0.0f}m -Xms{0:0.0f}m -Xmn{1:0.0f}m -XX:SurvivorRatio={2:0.0f} -XX:MaxTenuringThreshold={3:0.0f} -XX:CMSInitiatingOccupancyFraction={4:0.0f}".format(recommended_max_heap_size / 1024.0 / 1024.0, float(adj_ng_size) / 1024.0 / 1024.0, survivor_ratio, max_tenuring_age, occ_fraction), textwrap_offset)) + "\n")

    if ready_for_g1:
        display("\n")
        display("The JVM arguments for G1:\n")
        display("~~~~~~~~~~~~~~~~~~~~~~~~~\n")
        display("\n".join(textwrap.wrap("-XX:+UseG1GC -XX:MaxGCPauseMillis={0:0.0f} -Xms{1:0.0f}m -Xmx{1:0.0f}m ".format(ygc_mean_ms, recommended_max_heap_size / 1024.0 / 1024.0), textwrap_offset)) + "\n")


def get_proc_info(pid=None):
    """Return a data structure with details of the given process id

    Keyword arguments:
    pid -- the process id of the process to be checked
    """

    details = dict()

    try:
        cpu_ticks_per_sec = int(os.sysconf(os.sysconf_names['SC_CLK_TCK']))
        bytes_per_page = resource.getpagesize()
        details['gc_file_rotation'] = False

        for line in liverun("readlink /proc/{0}/cwd".format(pid)):
            details['proc_cwd'] = line.strip()

        with open("/proc/{0}/cmdline".format(pid), "r") as _file:
            for blob in _file:
                for line in blob.split("\0"):
                    if "-Xloggc" in line:
                        gc_path = line.split(":", 1)[1]

                        if gc_path.startswith("/"):
                            details['gc_log_path'] = gc_path
                        else:
                            details['gc_log_path'] = details['proc_cwd'] + "/" + gc_path

                    elif "/bin/java" in line:
                        details['java_path'] = os.path.dirname(line)

                    elif "-XX:+UseGCLogFileRotation" in line:
                        details['gc_file_rotation'] = True

                    elif "-Xms" in line:
                        details['min_heap_size'] = line.split("ms")[1]

                    elif "-Xmx" in line:
                        details['max_heap_size'] = line.split("mx")[1]

                    elif "-XX:+PrintGCDateStamps" in line:
                        details['print_gc_date_stamps'] = True

                    elif "-XX:+PrintGCDetails" in line:
                        details['print_gc_details'] = True

                    elif "-XX:+PrintTenuringDistribution" in line:
                        details['print_tenuring_distribution'] = True

                    elif "-XX:SurvivorRatio=" in line:
                        details['survivor_ratio'] = line.split("SurvivorRatio=")[1]

                    elif "-XX:+UseConcMarkSweepGC" in line:
                        details['use_cms'] = True

                    elif "-XX:+UseParNewGC" in line:
                        details['use_parnew'] = True

            if 'java_path' not in details:
                details['java_path'] = ''.join(liverun("which java")).strip().replace("/java", "")

        with open("/proc/uptime".format(pid), "r") as _file:
            for line in _file:
                details['sys_uptime_seconds'] = Decimal(line.split()[0])
                break

        with open("/proc/{0}/stat".format(pid), "r") as _file:
            for line in _file:
                field = line.split()

                utime_ticks = int(field[13])
                stime_ticks = int(field[14])
                num_threads = int(field[19])
                uptime_ticks = int(field[21])
                vsize_bytes = int(field[22])
                rss_bytes = int(field[23]) * bytes_per_page

                details['proc_uptime_seconds'] = (details['sys_uptime_seconds']) - Decimal(str(uptime_ticks / float(cpu_ticks_per_sec)))
                details['proc_utime_seconds'] = utime_ticks / Decimal(cpu_ticks_per_sec)
                details['proc_stime_seconds'] = stime_ticks / Decimal(cpu_ticks_per_sec)
                details['proc_rss_bytes'] = rss_bytes
                details['proc_vsize_bytes'] = vsize_bytes
                details['num_threads'] = num_threads

                break

        for line in liverun("{0}/java -version".format(details['java_path'])):
            if "java version" in line:
                line = line.strip().replace("\"", "")

                fields = line.split()

                details['java_build_version'] = fields[-1]

                match = re.match(r"^(\d+)\.(\d+)\.(\d+)", details['java_build_version'])
                details['java_ver_int'] = match.group(2)

                break

    except IOError:
        # The data structure will be empty, and I'll catch it when
        # I get a key error on accessing it
        pass

    return details


def process_gclog(log_file=None, log_file_pos=0):
    """Pretty basic function that iterates through a gc log, and returns a data structure
    of the log data.

    Keyword arguments:
    log_file -- the gc log file to be read
    log_file_pos -- the offset of the log file from whence to start (as bytes)
    """

    gc_log_queue = list()

    try:
        line_num = 0

        print ""
        print "* Reading gc.log file...",

        current_size = os.stat(log_file).st_size
        if current_size < log_file_pos:
            print "log file was truncated/rotated; reading from the start",
            log_file_pos = 0

        start_time = datetime.datetime.now()

        with open(log_file, "r") as _file:
            _file.seek(log_file_pos)

            for line in _file:
                gc_log_queue.append(line)
                line_num += 1

        elapsed_time = sec_diff(start_time, datetime.datetime.now())

        print "done. Scanned {0} lines in {1:0.4f} seconds.".format(line_num, elapsed_time)
    except IOError:
        # I don't want/need to check the exception. If it fails, it fails.
        pass
    else:
        gc_log_queue.append("END_OF_FILE")

    return gc_log_queue


def _run_jmap(pid=None, procdetails=None):
    """Rung jmap for the given process id, and java path, returning
    a data structure with the information"""

    jmap_data = dict()
    java_path = procdetails['java_path']

    try:
        for line in liverun("{0}/jmap -J-Xmx128M -heap {1}".format(java_path, pid)):
            field = line.split()

            if "MinHeapFreeRatio" in line:
                jmap_data['MinHeapFreeRatio'] = int(field[2])

            elif "MaxHeapFreeRatio" in line:
                jmap_data['MaxHeapFreeRatio'] = int(field[2])

            elif "MaxHeapSize" in line:
                jmap_data['MaxHeapSize'] = int(field[2])

            elif "NewSize" in line:
                jmap_data['NewSize'] = int(field[2])

            elif "MaxNewSize" in line:
                jmap_data['MaxNewSize'] = int(field[2])

            elif "OldSize" in line:
                # JMap seems to be scaled wrong. Comparing it to jstat, it shows that
                # it's off by about 1000 (1024). There's a bug in Java6 where this is in KB
                # not bytes like the others. Appears to be fixed in Java8 (maybe Java7, too)
                java_int = procdetails['java_ver_int']

                if java_int < 8:
                    jmap_data['OldSize'] = int(field[2]) * 1024
                else:
                    jmap_data['OldSize'] = int(field[2])

            elif "NewRatio" in line:
                jmap_data['NewRatio'] = int(field[2])

            elif "SurvivorRatio" in line:
                jmap_data['SurvivorRatio'] = int(field[2])

            elif "PermSize" in line:
                jmap_data['PermSize'] = int(field[2])

            elif "MaxPermSize" in line:
                jmap_data['MaxPermSize'] = int(field[2])

            elif "MaxMetaspaceSize" in line:
                if "MB" in line:
                    jmap_data['MaxMetaspaceSize'] = int(field[2]) * 1024 * 1024
                else:
                    jmap_data['MaxMetaspaceSize'] = int(field[2])

            elif "MetaspaceSize" in line:
                jmap_data['MetaspaceSize'] = int(field[2])

    except (IOError, KeyboardInterrupt):
        pass

    return jmap_data


def run_jstat(pid=None, java_path=None, no_jstat_output=None, fgc_stop_count=None, max_count=None, ygc_stop_count=None):
    """Rung jstat, and outputs the data in a nice column and aligned layout.

    Keyword arguments:
    pid -- the process pid to run jstat against
    java_path -- the path to use to run jstat
    no_jstat_output -- true/false that tells this function to not output any data
    fgc_stop_count -- the integer value that tells this function to stop at this number of full (cms) gcs
    max_count -- the max number of lines the function should display
    ygc_stop_count -- the integer value that tells this function to stop at this number of young gcs
    """
    global subproc

    jstat_data = dict()
    jstat_data['TIME_STAMP'] = list()

    # This is how the columns will be displayed in order.
    ordered_fields = ["EC", "EP", "EU", "S0C/S1C", "S0C", "S1C", "S0U", "S1U", "OC", "OP", "OU", "MC", "MU", "PC", "PU", "YGC", "YGCD", "FGC", "FGCD"]
    # ordered_fields = ["EC", "EP", "EU", "S0C/S1C", "S0C", "S1C", "S0U", "S1U", "OC", "OP", "OU", "MC", "MU", "PC", "PU", "YGC", "YGCT", "FGC", "FGCT", "GCT"]

    displayed_output = False
    combined_survivors = False

    field_map = dict()
    line_num = 0
    field_widths = dict()

    first_fgc_ct = None
    prev_fgc_ct = None
    last_fgc_ct = None
    total_fgcs = None
    total_ygcs = None

    short_fields = True

    # Being able to use python3's print function that I could override would
    # work much better here; instead I have to do this ghetto way...
    display("#" * 5 + "\n")
    display("# Start Time:  {0} GMT\n".format(datetime.datetime.now()))
    display("# Host:        {0}\n".format(socket.getfqdn()))
    display("#" * 5 + "\n")

    if max_count > 0:
        cmd = "{0}/jstat -J-Xmx128M -gc {1} 1000 {2}".format(java_path, pid, max_count)
    else:
        cmd = "{0}/jstat -J-Xmx128M -gc {1} 1000".format(java_path, pid)

    try:
        for line in liverun(cmd):
            timestamp = datetime.datetime.now()
            line = line.strip()

            #######################################################################
            # Print the header, and first two lines should be printed. After that,
            # the logic block at the end (to see if there's been a fgc or not)
            # takes over, and prints the line conditionally with decoration
            field_num = 0

            for field in line.split():
                if line_num == 0:
                    jstat_data[field] = list()
                    field_map[field_num] = field
                else:
                    field_name = field_map[field_num]

                    if field_name in ['YGCT', 'FGCT', 'GCT']:
                        jstat_data[field_name].append(Decimal(field))
                    else:
                        # Minding sigfigs- no decimal needed for large numbers; that's
                        # just silly
                        jstat_data[field_name].append(Decimal("{0:0.0f}".format(Decimal(field))))

                field_num += 1

            if jstat_data['OC'] and jstat_data['OU']:
                # Better to handle the percentage-awareness here instead
                # of making a unique conditional later on
                if "OP" not in jstat_data:
                    jstat_data['OP'] = list()

                jstat_data['OP'].append("{0:0.1%}".format(jstat_data['OU'][-1] / jstat_data['OC'][-1]))

            if jstat_data['EC'] and jstat_data['EU']:
                # Better to handle the percentage-awareness here instead
                # of making a unique conditional later on
                if "EP" not in jstat_data:
                    jstat_data['EP'] = list()

                jstat_data['EP'].append("{0:0.1%}".format(jstat_data['EU'][-1] / jstat_data['EC'][-1]))

            if jstat_data['GCT']:
                if "YGCD" not in jstat_data:
                    jstat_data['YGCD'] = list()

                if "FGCD" not in jstat_data:
                    jstat_data['FGCD'] = list()

                # Young gc count delta
                try:
                    if jstat_data['YGC'][-1] > jstat_data['YGC'][-2]:
                        delta = "+" + str(jstat_data['YGC'][-1] - jstat_data['YGC'][-2])
                    else:
                        delta = "-"
                except IndexError:
                    delta = "-"

                jstat_data['YGCD'].append(delta)

                # full gc count delta
                try:
                    if jstat_data['FGC'][-1] > jstat_data['FGC'][-2]:
                        delta = "+" + str(jstat_data['FGC'][-1] - jstat_data['FGC'][-2])
                    else:
                        delta = "-"
                except IndexError:
                    delta = "-"

                jstat_data['FGCD'].append(delta)

            ##################################
            # I need at least two lines to get
            # historical data
            if line_num >= 2:
                # Keep a timestamp for each record (to get sub-second granularity)
                first_fgc_ct = jstat_data['FGC'][0]
                first_ygc_ct = jstat_data['YGC'][0]
                prev_fgc_ct = jstat_data['FGC'][-2]
                last_fgc_ct = jstat_data['FGC'][-1]
                prev_ygc_ct = jstat_data['YGC'][-2]
                last_ygc_ct = jstat_data['YGC'][-1]
                total_fgcs = last_fgc_ct - first_fgc_ct
                total_ygcs = last_ygc_ct - first_ygc_ct

            #############################################
            # line 1 is actual data, 0 is just the header
            if line_num > 0:
                jstat_data['TIME_STAMP'].append(timestamp)

                ####################################################
                # See if I can combine the S0C/S1C fields (probably)
                if jstat_data['S0C'][-1] == jstat_data['S1C'][-1]:
                    if "S0C/S1C" not in jstat_data:
                        jstat_data['S0C/S1C'] = list()

                    jstat_data['S0C/S1C'].append(jstat_data['S0C'][-1])
                    combined_survivors = True
                else:
                    # This is redundant as I catch it earlier. Leaving it here for now.
                    logger.error("Looks like you're not running with the CMS garbage collector. You can enable this option by setting your JVM arguments to use '-XX:+UseConcMarkSweepGC'.")
                    sys.exit(1)

                if not field_widths:
                    field_widths = _get_widths(jstat_data, short_fields)

                if not displayed_output:
                    displayed_output = True

                    #############################################
                    # Don't display any output, just continue to
                    # the next iteration. Ick, double-negative..
                    if no_jstat_output:
                        continue

                    # Print the column header
                    display("  ", keep_newline=False)
                    for field in ordered_fields:
                        if combined_survivors and field != "S0C" and field != "S1C":
                            if field in field_widths:
                                width = field_widths[field]
                                display("{0:>{1}}".format(field, width + 1), keep_newline=False)

                    display("\n")

                    # Print a nice line spacer all even-like
                    display("  ", keep_newline=False)
                    for field in ordered_fields:
                        if combined_survivors and field != "S0C" and field != "S1C":
                            if field in field_widths:
                                width = field_widths[field]
                                display("{0:>{1}}".format("~" * width, width + 1), keep_newline=False)

                    display("\n")

                    # Print the first row of data that was cached so it can
                    # be used to determine field widths
                    display("  ", keep_newline=False)
                    for field in ordered_fields:
                        if field in field_widths:
                            width = field_widths[field]

                            # Get the last value
                            if combined_survivors and field != "S0C" and field != "S1C":
                                value = jstat_data[field][0]

                                if short_fields and field not in ['EP', 'OP', 'YGC', 'YGCT', 'FGC', 'FGCT', 'GCT', 'FGCD', 'YGCD']:
                                    value = reduce_k(value, precision=1)

                                display("{0:>{1}}".format(value, width + 1), keep_newline=False)

                    display("\n")

                else:
                    #################################
                    # Don't display any output, just
                    # continue to the next iteration.
                    if no_jstat_output:
                        if last_fgc_ct > prev_fgc_ct:
                            display("* ", keep_newline=False)
                        else:
                            display("  ", keep_newline=False)

                        # Now print the actual numbers
                        for field in ordered_fields:
                            if field in field_widths:
                                width = field_widths[field]

                                # Get the last value
                                if combined_survivors and field != "S0C" and field != "S1C":
                                    value = jstat_data[field][-1]

                                    if short_fields and field not in ['EP', 'OP', 'YGC', 'YGCT', 'FGC', 'FGCT', 'GCT', 'FGCD', 'YGCD']:
                                        value = reduce_k(value, precision=1)

                                    display("{0:>{1}}".format(value, width + 1), keep_newline=False)

                        display("\n")
                    else:

                        if last_fgc_ct > prev_fgc_ct:
                            display("* ", keep_newline=False)
                        else:
                            display("  ", keep_newline=False)

                        # Now print the actual numbers
                        for field in ordered_fields:
                            if field in field_widths:
                                width = field_widths[field]

                                # Get the last value
                                if combined_survivors and field != "S0C" and field != "S1C":
                                    value = jstat_data[field][-1]

                                    if short_fields and field not in ['EP', 'OP', 'YGC', 'YGCT', 'FGC', 'FGCT', 'GCT', 'FGCD', 'YGCD']:
                                        value = reduce_k(value, precision=1)

                                    display("{0:>{1}}".format(value, width + 1), keep_newline=False)

                        display("\n")

            if 0 < fgc_stop_count <= total_fgcs:
                break

            if 0 < ygc_stop_count <= total_ygcs:
                break

            line_num += 1

    except (IOError, KeyboardInterrupt):
        # This triggers if I exit the 'liverun'
        pass
    finally:
        if subproc and subproc.poll() is None:
            # The process hasn't terminated
            subproc.terminate()

    return jstat_data


def _get_widths(jstat_data=None, short_fields=False):
    """Function that returns the recommended field widths of the jstat output"""

    widths = dict()

    for field in jstat_data:
        max_width = max(map(len, map(str, jstat_data[field])))
        field_width = len(field)

        if field_width > max_width:
            widths[field] = field_width
        else:
            widths[field] = max_width

    ##################################################################
    # Special handling for survivor spaces (S0C, S1C, S0U, S1U) should
    # all be the same width, and b/c S{01}U alternate, it's better to
    # set the width from S{01}C

    if short_fields:
        # The '5' accounts for 'x.xxN' (3.23K/M/G), etc.
        survivor_max = 6
        newgen_max = 6
        oldgen_max = 6
    else:
        survivor_max = max(widths['S0C'], widths['S1C'], widths['S0U'], widths['S1U'])
        newgen_max = max(widths['EC'], widths['EU'])
        oldgen_max = max(widths['OC'], widths['OU'])

    widths['OC'] = oldgen_max
    widths['OU'] = oldgen_max

    widths['EC'] = newgen_max
    widths['EU'] = newgen_max

    widths['S0C'] = survivor_max
    widths['S1C'] = survivor_max
    widths['S0U'] = survivor_max
    widths['S1U'] = survivor_max

    widths['EP'] = 6
    widths['OP'] = 6

    return widths


def _at_exit(raw_gc_log=None, jmap_data=None, jstat_data=None, proc_details=None, replay_file=None, optimized_for_ygcs_rate=None):
    """The exit function that is called when the user presses ctrl-c, or when it exits after X number
    of jstat iterations. It calls various functions to display useful information to the end-user."""

    gc_data = list()
    in_stanza = False
    date_time = None
    entry = list()

    # I don't know if I like this, but I wouldn't get to
    # this point unless I asked for GC data from stdin...
    if not raw_gc_log:
        raw_gc_log = sys.stdin

    for line in raw_gc_log:
        #############################################################################
        # Since I'm using the timestamp as the record stanza delimiter, I may as well
        # convert it to a datetime object here instead of doing it later.
        match = re.match(r"^(\d+)-(\d+)-(\d+)T(\d+):(\d+):([\d\.]+)[+-]\d+: ([\d\.]+):", line)

        if match:
            in_stanza = True

            # If I'm at the start of a new block, save the previous block
            if date_time and entry:
                gc_record = GCRecord((date_time, entry))

                if gc_data:
                    prev_gc_record = gc_data[-1]

                    if gc_record.jvm_running_time and prev_gc_record.jvm_running_time > gc_record.jvm_running_time:
                        logger.warning("The JVM restarted at {0}. Re-initing the internal datastructures.".format(gc_record.record_timestamp))
                        gc_data = list()

                if gc_record.valid_record:
                    gc_data.append(gc_record)

            entry = list()

            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            hour = int(match.group(4))
            minute = int(match.group(5))
            second = Decimal(match.group(6))
            # up_time = Decimal(match.group(7))

            date_time = datetime.datetime.strptime("{0}-{1}-{2} {3}:{4}:{5}".format(year, month, day, hour, minute, second), "%Y-%m-%d %H:%M:%S.%f")

        if in_stanza:
            entry.append(line)

    _run_analysis(gc_data, jmap_data, jstat_data, proc_details, replay_file, optimized_for_ygcs_rate)


def get_rotated_log_file(gc_log_file):
    """Function will scan existing log files to determine latest rotated log, if none found will return
       non rotated file name.
    """
    log_number = 0
    while os.path.isfile("{0}.{1}".format(gc_log_file, log_number)):
        log_number += 1

    if log_number:
        gc_log_file = "{0}.{1}".format(gc_log_file, (log_number - 1))
    else:
        logger.debug("\n".join(
            textwrap.wrap(
                "Was not able to find a rotated GC log for this process, defaulting to gc log from process.",
                textwrap_offset)))

    return gc_log_file


def get_gc_log_file(procdetails):
    gc_log_file = procdetails['gc_log_path']

    if not gc_log_file:
        logger.error("\n".join(
            textwrap.wrap(
                "I was not able to find a GC log for this process. Is the instance up?",
                textwrap_offset)))
        sys.exit(1)

    if procdetails['gc_file_rotation']:
        return get_rotated_log_file(gc_log_file)
    else:
        return gc_log_file


def get_jmap_data(pid=None, procdetails=None):
    """Function that runs jmap, only needed b/c jmap may not start, and this retries on failure."""

    jmap_data = None

    for seconds in [x * 2 for x in range(1, 8)]:
        jmap_data = _run_jmap(pid, procdetails)

        if "NewSize" in jmap_data:
            break
        else:
            logger.warning("Couldn't connect to jvm via jmap to get valid data. Sleeping {0:0.0f} seconds, and trying again.".format(seconds))
            time.sleep(seconds)

    return jmap_data


################################################################
# Main
user = os.environ.get("SUDO_USER", None)

if not user:
    user = getpass.getuser()

subproc = None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analytics tool for tuning and analyzing GC behavior.")
    parser.add_argument('-o', '--optimize', help='Optimize for latency or throughput (range 0-11, 0 = ygc @ 180/min, 11 = ygc @  1/min). Floats allowed.', type=Decimal, required=False, default=9)
    parser.add_argument('-s', '--fgc-stop-count', help='How many full gcs should happen before I stop (very important for analytics)', type=int, default=0)
    parser.add_argument('-y', '--ygc-stop-count', help='How many young gcs should happen before I stop', type=int, default=0)
    parser.add_argument('-c', '--stop-count', help='How many iterations of jstat to run before stopping', type=int, default=0)
    parser.add_argument('-n', '--no-jstat-output', help='Do not show jstat output - only print summary', action="store_true")

    if DEBUG:
        group = parser.add_mutually_exclusive_group(required=False)
    else:
        group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument('-r', '--replay', dest="replay_file", const="/tmp/jtune_data-{0}.bin.bz2".format(user), help="Replay a previously saved default is /tmp/jtune_data-{0}.bin.bz2 file".format(user), metavar="FILE", nargs="?", default=None)
    group.add_argument('-p', '--pid', help='Which java PID should I attach to', type=int)
    group.add_argument('--gc-stdin', help='Read GC log data from stdin', action="store_true")

    cmd_args = parser.parse_args()

    replay_file = cmd_args.replay_file
    raw_gc_log_data = list()
    jmap_data = list()
    jstat_data = list()
    proc_details = list()
    display_output = list()

    if DEBUG:
        # Need to define it here for Pycharm (so I don't
        # have to set up arguments).
        replay_file = "/tmp/some_file"

    if not (cmd_args.pid or cmd_args.gc_stdin) and not os.path.isfile(replay_file):
        logger.error("The replay file '{0}' does not exist, or is not a file.".format(replay_file))
        sys.exit(1)

    textwrap_offset = 80

    # A ygc of 1/min
    ygc_lower_rate_per_min = 1

    # A ygc of 180/min (3/sec)
    ygc_upper_rate_per_min = 180

    # Validate the optimize range
    if 0 <= cmd_args.optimize <= 11:
        # You won't have to change this function if you want
        # to change the ygc upper/lower bounds later on
        #
        # Convert from rate/min to rate/sec
        optimized_for_ygcs_rate = ((-Decimal(ygc_upper_rate_per_min - 1) / 11) * Decimal(str(cmd_args.optimize)) + ygc_upper_rate_per_min)
    else:
        logger.error("The optimize range must be between 0 and 11.")
        sys.exit(1)

    ######################################################################
    # This should be done w/ argparse, but I haven't dedicated enough time
    # to figure it out
    if cmd_args.no_jstat_output and not (cmd_args.ygc_stop_count or cmd_args.stop_count or cmd_args.fgc_stop_count):
        logger.error("You must specify -s, -y, or -c arguments for this option to work.")
        sys.exit(1)

    if replay_file:
        try:
            with open(replay_file, "rb") as _file:
                proc_details, jstat_data, display_output, jmap_data, raw_gc_log_data = pickle.loads(_file.read().decode('bz2'))
        except (ValueError, IOError):
            logger.error("I was not able to read the replay file. Exiting.")
            sys.exit(1)
        else:
            print "* Note: Used cached data found in {0}.".format(replay_file)
    else:
        if not cmd_args.gc_stdin:
            try:
                config_error = False
                proc_details = get_proc_info(cmd_args.pid)

                java_path, proc_uptime = proc_details['java_path'], proc_details['proc_uptime_seconds']

                if proc_details.get("min_heap_size", 0) != proc_details.get("max_heap_size", 1):
                    config_error = True
                    logger.error(
                        "It looks like either you didn't specify your min and max heap size (-Xms & -Xmx respectively), or they are set to two different sizes. They need to be set to the same for jtune.py to work properly.")

                if not proc_details.get("print_gc_date_stamps", False):
                    config_error = True
                    logger.error("You need to include the '-XX:PrintGCDateStamps' option to the JVM for JTune to work correctly.")

                if not proc_details.get("print_gc_details", False):
                    config_error = True
                    logger.error("You need to include the '-XX:PrintGCDetails' option to the JVM for JTune to work correctly.")

                if not proc_details.get("print_tenuring_distribution", False):
                    config_error = True
                    logger.error("You need to include the '-XX:+PrintTenuringDistribution' option to the JVM for JTune to work correctly.")

                if not proc_details.get("survivor_ratio", False):
                    logger.warning("You probably want to include the '-XX:SurvivorRatio=<num>' option to the JVM for JTune to work correctly.")

                if not proc_details.get("use_cms", False):
                    config_error = True
                    logger.error("You need to include the '-XX:+UseConcMarkSweepGC' option to the JVM for JTune to work correctly.")

                if not proc_details.get("use_parnew", False):
                    config_error = True
                    logger.error("You need to include the '-XX:+UseParNewGC' option to the JVM for JTune to work correctly.")

                if config_error:
                    logger.error("Exiting.")
                    sys.exit(1)

            except (TypeError, KeyError):
                logger.error("I was not able to get the process data for pid {0}".format(cmd_args.pid))
                sys.exit(1)

            ###########################################
            # Start the gc log watching in a subprocess
            back_secs = 300
            gc_log_file = get_gc_log_file(proc_details)

            if not gc_log_file:
                logger.error("\n".join(textwrap.wrap("I was not able to find a GC log for this process. Is the instance up?", textwrap_offset)))
                sys.exit(1)

            ####################################################
            # Get the file offset before starting jstat, so
            # I can use it after jstat runs to read the log file
            gc_log_file_pos = os.stat(gc_log_file).st_size

            jmap_data = get_jmap_data(cmd_args.pid, proc_details)

            if cmd_args.no_jstat_output:
                jstat_data = dict()
            else:
                jstat_data = run_jstat(cmd_args.pid, java_path, cmd_args.no_jstat_output, cmd_args.fgc_stop_count, cmd_args.stop_count, cmd_args.ygc_stop_count)

            # This basically hits after the user ctrl-c's
            raw_gc_log_data = process_gclog(gc_log_file, gc_log_file_pos)


        #####################################################
        # Keep the last dump of data in case there's an issue
        try:
            with open("/tmp/jtune_data-{0}.bin.bz2".format(user), "wb") as _file:
                os.chmod("/tmp/jtune_data-{0}.bin.bz2".format(user), 0666)
                _file.write(pickle.dumps((proc_details, jstat_data, display_output, jmap_data, raw_gc_log_data), pickle.HIGHEST_PROTOCOL).encode('bz2'))
        except IOError as msg:
            logger.error("\n".join(textwrap.wrap("I was not able to write to /tmp/jtune_data-{0}.bin.bz2 (no saving of state): {1}".format(user, msg), textwrap_offset)))

    if DEBUG:
        _at_exit(raw_gc_log_data, jmap_data, jstat_data, proc_details, replay_file, optimized_for_ygcs_rate)
    else:
        atexit.register(_at_exit, raw_gc_log_data, jmap_data, jstat_data, proc_details, replay_file, optimized_for_ygcs_rate)
