#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Paul Nilsson, paul.nilsson@cern.ch, 2017-2022

import os
import re
import sys
import logging

from collections.abc import Set, Mapping
from collections import deque, OrderedDict
from numbers import Number
from time import sleep

from pilot.util.constants import (
    SUCCESS,
    FAILURE,
    SERVER_UPDATE_FINAL,
    SERVER_UPDATE_NOT_DONE,
    SERVER_UPDATE_TROUBLE,
    get_pilot_version,
)

from pilot.common.errorcodes import ErrorCodes
from pilot.util.container import execute
from pilot.util.filehandling import dump

zero_depth_bases = (str, bytes, Number, range, bytearray)
iteritems = 'items'
logger = logging.getLogger(__name__)
errors = ErrorCodes()


def pilot_version_banner():
    """
    Print a pilot version banner.

    :return:
    """

    version = '***  PanDA Pilot version %s  ***' % get_pilot_version()
    logger.info('*' * len(version))
    logger.info(version)
    logger.info('*' * len(version))
    logger.info('')

    if is_virtual_machine():
        logger.info('pilot is running in a VM')

    display_architecture_info()
    logger.info('*' * len(version))


def is_virtual_machine():
    """
    Are we running in a virtual machine?
    If we are running inside a VM, then linux will put 'hypervisor' in cpuinfo. This function looks for the presence
    of that.

    :return: boolean.
    """

    status = False

    # look for 'hypervisor' in cpuinfo
    with open("/proc/cpuinfo", "r") as _fd:
        lines = _fd.readlines()
        for line in lines:
            if "hypervisor" in line:
                status = True
                break

    return status


def display_architecture_info():
    """
    Display OS/architecture information.
    The function attempts to use the lsb_release -a command if available. If that is not available,
    it will dump the contents of
    WARNING: lsb_release will not be available on CentOS Stream 9

    :return:
    """

    logger.info("architecture information:")

    _, stdout, stderr = execute("lsb_release -a", mute=True)
    if 'Command not found' in stdout or 'Command not found' in stderr:
        # Dump standard architecture info files if available
        dump("/etc/lsb-release")
        dump("/etc/SuSE-release")
        dump("/etc/redhat-release")
        dump("/etc/debian_version")
        dump("/etc/issue")
        dump("$MACHTYPE", cmd="echo")
    else:
        logger.info("\n%s", stdout)


def get_batchsystem_jobid():
    """
    Identify and return the batch system job id (will be reported to the server)

    :return: batch system job id
    """

    # BQS (e.g. LYON)
    batchsystem_dict = {'QSUB_REQNAME': 'BQS',
                        'BQSCLUSTER': 'BQS',  # BQS alternative
                        'PBS_JOBID': 'Torque',
                        'LSB_JOBID': 'LSF',
                        'JOB_ID': 'Grid Engine',  # Sun's Grid Engine
                        'clusterid': 'Condor',  # Condor (variable sent through job submit file)
                        'SLURM_JOB_ID': 'SLURM',
                        'K8S_JOB_ID': 'Kubernetes'}

    for key, value in list(batchsystem_dict.items()):
        if key in os.environ:
            return value, os.environ.get(key, '')

    # Condor (get jobid from classad file)
    if '_CONDOR_JOB_AD' in os.environ:
        try:
            with open(os.environ.get("_CONDOR_JOB_AD"), 'r') as _fp:
                for line in _fp:
                    res = re.search(r'^GlobalJobId\s*=\s*"(.*)"', line)
                    if res is None:
                        continue
                    return "Condor", res.group(1)
        except OSError as exc:
            logger.warning("failed to read HTCondor job classAd: %s", exc)
    return None, ""


def get_job_scheduler_id():
    """
    Get the job scheduler id from the environment variable PANDA_JSID

    :return: job scheduler id (string)
    """
    return os.environ.get("PANDA_JSID", "unknown")


def whoami():
    """
    Return the name of the pilot user.

    :return: whoami output (string).
    """

    _, who_am_i, _ = execute('whoami', mute=True)

    return who_am_i


def get_error_code_translation_dictionary():
    """
    Define the error code translation dictionary.

    :return: populated error code translation dictionary.
    """

    error_code_translation_dictionary = {
        -1: [64, "Site offline"],
        errors.GENERALERROR: [65, "General pilot error, consult batch log"],  # added to traces object
        errors.MKDIR: [66, "Could not create directory"],  # added to traces object
        errors.NOSUCHFILE: [67, "No such file or directory"],  # added to traces object
        errors.NOVOMSPROXY: [68, "Voms proxy not valid"],  # added to traces object
        errors.NOPROXY: [68, "Proxy not valid"],  # added to traces object
        errors.CERTIFICATEHASEXPIRED: [68, "Proxy not valid"],
        errors.NOLOCALSPACE: [69, "No space left on local disk"],  # added to traces object
        errors.UNKNOWNEXCEPTION: [70, "Exception caught by pilot"],  # added to traces object
        errors.QUEUEDATA: [71, "Pilot could not download queuedata"],  # tested
        errors.QUEUEDATANOTOK: [72, "Pilot found non-valid queuedata"],  # not implemented yet, error code added
        errors.NOSOFTWAREDIR: [73, "Software directory does not exist"],  # added to traces object
        errors.JSONRETRIEVALTIMEOUT: [74, "JSON retrieval timed out"],  # ..
        errors.BLACKHOLE: [75, "Black hole detected in file system"],  # ..
        errors.MIDDLEWAREIMPORTFAILURE: [76, "Failed to import middleware module"],  # added to traces object
        errors.MISSINGINPUTFILE: [77, "Missing input file in SE"],  # should pilot report this type of error to wrapper?
        errors.PANDAQUEUENOTACTIVE: [78, "PanDA queue is not active"],
        errors.KILLSIGNAL: [137, "General kill signal"],  # Job terminated by unknown kill signal
        errors.SIGTERM: [143, "Job killed by signal: SIGTERM"],  # 128+15
        errors.SIGQUIT: [131, "Job killed by signal: SIGQUIT"],  # 128+3
        errors.SIGSEGV: [139, "Job killed by signal: SIGSEGV"],  # 128+11
        errors.SIGXCPU: [158, "Job killed by signal: SIGXCPU"],  # 128+30
        errors.SIGUSR1: [144, "Job killed by signal: SIGUSR1"],  # 128+16
        errors.SIGBUS: [138, "Job killed by signal: SIGBUS"]   # 128+10
    }

    return error_code_translation_dictionary


def shell_exit_code(exit_code):
    """
    Translate the pilot exit code to a proper exit code for the shell (wrapper).
    Any error code that is to be converted by this function, should be added to the traces object like:
      traces.pilot['error_code'] = errors.<ERRORCODE>
    The traces object will be checked by the pilot module.

    :param exit_code: pilot error code (int).
    :return: standard shell exit code (int).
    """

    # Error code translation dictionary
    # FORMAT: { pilot_error_code : [ shell_error_code, meaning ], .. }

    # Restricting user (pilot) exit codes to the range 64 - 113, as suggested by http://tldp.org/LDP/abs/html/exitcodes.html
    # Using exit code 137 for kill signal error codes (this actually means a hard kill signal 9, (128+9), 128+2 would mean CTRL+C)

    error_code_translation_dictionary = get_error_code_translation_dictionary()

    if exit_code in error_code_translation_dictionary:
        return error_code_translation_dictionary.get(exit_code)[0]  # Only return the shell exit code, not the error meaning
    elif exit_code != 0:
        print("no translation to shell exit code for error code %d" % exit_code)
        return FAILURE
    else:
        return SUCCESS


def convert_to_pilot_error_code(exit_code):
    """
    This conversion function is used to revert a batch system exit code back to a pilot error code.
    Note: the function is used by Harvester.

    :param exit_code: batch system exit code (int).
    :return: pilot error code (int).
    """
    error_code_translation_dictionary = get_error_code_translation_dictionary()

    list_of_keys = [key for (key, value) in error_code_translation_dictionary.items() if value[0] == exit_code]
    # note: do not use logging object as this function is used by Harvester
    if not list_of_keys:
        print('unknown exit code: %d (no matching pilot error code)' % exit_code)
        list_of_keys = [-1]
    elif len(list_of_keys) > 1:
        print('found multiple pilot error codes: %s' % list_of_keys)

    return list_of_keys[0]


def get_size(obj_0):
    """
    Recursively iterate to sum size of object & members.
    Note: for size measurement to work, the object must have set the data members in the __init__().

    :param obj_0: object to be measured.
    :return: size in Bytes (int).
    """

    _seen_ids = set()

    def inner(obj):
        obj_id = id(obj)
        if obj_id in _seen_ids:
            return 0

        _seen_ids.add(obj_id)
        size = sys.getsizeof(obj)
        if isinstance(obj, zero_depth_bases):
            pass  # bypass remaining control flow and return
        elif isinstance(obj, OrderedDict):
            pass  # can currently not handle this
        elif isinstance(obj, (tuple, list, Set, deque)):
            size += sum(inner(i) for i in obj)
        elif isinstance(obj, Mapping) or hasattr(obj, iteritems):
            try:
                size += sum(inner(k) + inner(v) for k, v in getattr(obj, iteritems)())
            except Exception:  # as e
                pass
                # <class 'collections.OrderedDict'>: unbound method iteritems() must be called
                # with OrderedDict instance as first argument (got nothing instead)
                #logger.debug('exception caught for obj=%s: %s', (str(obj), e))

        # Check for custom object instances - may subclass above too
        if hasattr(obj, '__dict__'):
            size += inner(vars(obj))
        if hasattr(obj, '__slots__'):  # can have __slots__ with __dict__
            size += sum(inner(getattr(obj, s)) for s in obj.__slots__ if hasattr(obj, s))

        return size

    return inner(obj_0)


def get_pilot_state(job=None):
    """
    Return the current pilot (job) state.
    If the job object does not exist, the environmental variable PILOT_JOB_STATE will be queried instead.

    :param job:
    :return: pilot (job) state (string).
    """

    return job.state if job else os.environ.get('PILOT_JOB_STATE', 'unknown')


def set_pilot_state(job=None, state=''):
    """
    Set the internal pilot state.
    Note: this function should update the global/singleton object but currently uses an environmental variable
    (PILOT_JOB_STATE).
    The function does not update job.state if it is already set to finished or failed.
    The environmental variable PILOT_JOB_STATE will always be set, in case the job object does not exist.

    :param job: optional job object.
    :param state: internal pilot state (string).
    :return:
    """

    os.environ['PILOT_JOB_STATE'] = state

    if job and job.state != 'failed':
        job.state = state


def check_for_final_server_update(update_server):
    """
    Do not set graceful stop if pilot has not finished sending the final job update
    i.e. wait until SERVER_UPDATE is DONE_FINAL. This function sleeps for a maximum
    of 20*30 s until SERVER_UPDATE env variable has been set to SERVER_UPDATE_FINAL.

    :param update_server: args.update_server boolean.
    :return:
    """

    max_i = 20
    i = 0

    # abort if in startup stage or if in final update stage
    server_update = os.environ.get('SERVER_UPDATE', '')
    if server_update == SERVER_UPDATE_NOT_DONE:
        return

    while i < max_i and update_server:
        server_update = os.environ.get('SERVER_UPDATE', '')
        if server_update == SERVER_UPDATE_FINAL or server_update == SERVER_UPDATE_TROUBLE:
            logger.info('server update done, finishing')
            break
        logger.info('server update not finished (#%d/#%d)', i + 1, max_i)
        sleep(30)
        i += 1


def get_resource_name():
    """
    Return the name of the resource (only set for HPC resources; e.g. Cori, otherwise return 'grid').

    :return: resource_name (string).
    """

    resource_name = os.environ.get('PILOT_RESOURCE_NAME', '').lower()
    if not resource_name:
        resource_name = 'grid'
    return resource_name


def get_object_size(obj, seen=None):
    """
    Recursively find the size of any objects

    :param obj: object.
    """

    size = sys.getsizeof(obj)
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0

    # Important mark as seen *before* entering recursion to gracefully handle
    # self-referential objects
    seen.add(obj_id)
    if isinstance(obj, dict):
        size += sum([get_object_size(v, seen) for v in obj.values()])
        size += sum([get_object_size(k, seen) for k in obj.keys()])
    elif hasattr(obj, '__dict__'):
        size += get_object_size(obj.__dict__, seen)
    elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, bytearray)):
        size += sum([get_object_size(i, seen) for i in obj])

    return size


def show_memory_usage():
    """
    Display the current memory usage by the pilot process.

    :return:
    """

    _ec, _stdout, _stderr = get_memory_usage(os.getpid())
    try:
        _value = extract_memory_usage_value(_stdout)
    except Exception:
        _value = "(unknown)"
    logger.debug('current pilot memory usage:\n\n%s\n\nusage: %s kB\n', _stdout, _value)


def get_memory_usage(pid):
    """
    Return the memory usage string (ps auxf <pid>) for the given process.

    :param pid: process id (int).
    :return: ps exit code (int), stderr (strint), stdout (string).
    """

    return execute('ps aux -q %d' % pid)


def extract_memory_usage_value(output):
    """
    Extract the memory usage value from the ps output (in kB).

    # USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
    # usatlas1 13917  1.5  0.0 1324968 152832 ?      Sl   09:33   2:55 /bin/python2 ..
    # -> 152832 (kB)

    :param output: ps output (string).
    :return: memory value in kB (int).
    """

    memory_usage = 0
    for row in output.split('\n'):
        try:
            memory_usage = int(" ".join(row.split()).split(' ')[5])
        except Exception:
            pass
        else:
            break

    return memory_usage


def cut_output(txt, cutat=1024, separator='\n[...]\n'):
    """
    Cut the given string if longer that 2*cutat value.

    :param txt: text to be cut at position cutat (string).
    :param cutat: max length of uncut text (int).
    :param separator: separator text (string).
    :return: cut text (string).
    """

    if len(txt) > 2 * cutat:
        txt = txt[:cutat] + separator + txt[-cutat:]

    return txt


def has_instruction_set(instruction_set):
    """
    Determine whether a given CPU instruction set is available.
    The function will use grep to search in /proc/cpuinfo (both in upper and lower case).

    :param instruction_set: instruction set (e.g. AVX2) (string).
    :return: Boolean
    """

    status = False
    cmd = r"grep -o \'%s[^ ]*\|%s[^ ]*\' /proc/cpuinfo" % (instruction_set.lower(), instruction_set.upper())
    exit_code, stdout, stderr = execute(cmd)
    if not exit_code and not stderr:
        if instruction_set.lower() in stdout.split() or instruction_set.upper() in stdout.split():
            status = True

    return status


def has_instruction_sets(instruction_sets):
    """
    Determine whether a given list of CPU instruction sets is available.
    The function will use grep to search in /proc/cpuinfo (both in upper and lower case).
    Example: instruction_sets = ['AVX', 'AVX2', 'SSE4_2', 'XXX'] -> "AVX|AVX2|SSE4_2"
    :param instruction_sets: instruction set (e.g. AVX2) (string).
    :return: Boolean
    """

    ret = ''
    pattern = ''

    for instr in instruction_sets:
        pattern += r'\|%s[^ ]*\|%s[^ ]*' % (instr.lower(), instr.upper()) if pattern else r'%s[^ ]*\|%s[^ ]*' % (instr.lower(), instr.upper())
    cmd = "grep -o \'%s\' /proc/cpuinfo" % pattern

    exit_code, stdout, stderr = execute(cmd)
    if not exit_code and not stderr:
        for instr in instruction_sets:
            if instr.lower() in stdout.split() or instr.upper() in stdout.split():
                ret += '|%s' % instr.upper() if ret else instr.upper()

    return ret


def locate_core_file(cmd=None, pid=None):
    """
    Locate the core file produced by gdb.

    :param cmd: optional command containing pid corresponding to core file (string).
    :param pid: optional pid to use with core file (core.pid) (int).
    :return: path to core file (string).
    """

    path = None
    if not pid and cmd:
        pid = get_pid_from_command(cmd)
    if pid:
        filename = 'core.%d' % pid
        path = os.path.join(os.environ.get('PILOT_HOME', '.'), filename)
        if os.path.exists(path):
            logger.debug('found core file at: %s', path)

        else:
            logger.debug('did not find %s in %s', filename, path)
    else:
        logger.warning('cannot locate core file since pid could not be extracted from command')

    return path


def get_pid_from_command(cmd, pattern=r'gdb --pid (\d+)'):
    """
    Identify an explicit process id in the given command.

    Example:
        cmd = 'gdb --pid 19114 -ex \'generate-core-file\''
        -> pid = 19114

    :param cmd: command containing a pid (string).
    :param pattern: regex pattern (raw string).
    :return: pid (int).
    """

    pid = None
    match = re.search(pattern, cmd)
    if match:
        try:
            pid = int(match.group(1))
        except Exception:
            pid = None
    else:
        logger.warning('no match for pattern \'%s\' in command=\'%s\'', pattern, cmd)

    return pid


def list_hardware():
    """
    Execute lshw to list local hardware.

    :return: lshw output (string).
    """

    exit_code, stdout, stderr = execute('lshw -numeric -C display', mute=True)
    if 'Command not found' in stdout or 'Command not found' in stderr:
        stdout = ''
    return stdout


def get_display_info():
    """
    Extract the product and vendor from the lshw command.
    E.g.
           product: GD 5446 [1013:B8]
           vendor: Cirrus Logic [1013]
    -> GD 5446, Cirrus Logic

    :return: product (string), vendor (string).
    """

    vendor = ''
    product = ''
    stdout = list_hardware()
    if stdout:
        vendor_pattern = re.compile(r'vendor\:\ (.+)\ .')
        product_pattern = re.compile(r'product\:\ (.+)\ .')

        for line in stdout.split('\n'):
            if 'vendor' in line:
                result = re.findall(vendor_pattern, line)
                if result:
                    vendor = result[0]
            elif 'product' in line:
                result = re.findall(product_pattern, line)
                if result:
                    product = result[0]

    return product, vendor


def get_key_value(catchall, key='SOMEKEY'):
    """
    Return the value corresponding to key in catchall.
    :param catchall: catchall free string.
    :param key: key name (string).
    :return: value (string).
    """

    # ignore any non-key-value pairs that might be present in the catchall string
    _dic = dict(_str.split('=', 1) for _str in catchall.split() if '=' in _str)

    return _dic.get(key)


def is_string(obj):
    """
    Determine if the passed object is a string or not.

    :param obj: object (object type).
    :return: True if obj is a string (Boolean).
    """

    return True if isinstance(obj, str) else False


def find_pattern_in_list(input_list, pattern):
    """
    Search for the given pattern in the input list.

    :param input_list: list of string.
    :param pattern: regular expression pattern (raw string).
    :return: found string (or None).
    """

    found = None
    for line in input_list:
        out = re.search(pattern, line)
        if out:
            found = out[0]
            break

    return found


def sort_words(input_str):
    """
    Sort the words in a given string.
    E.g. input_str = 'bbb fff aaa' -> output_str = 'aaa bbb fff'

    :param input_str: input string.
    :return: sorted output string.
    """

    output_str = input_str
    try:
        tmp = output_str.split()
        tmp.sort()
        output_str = ' '.join(tmp)
    except (AttributeError, TypeError) as exc:
        logger.warning(f'failed to sort input string: {input_str}, exc={exc}')

    return output_str
