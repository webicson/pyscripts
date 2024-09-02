#!/usr/bin/python
#
# 
# Author: Vladimir Gaitner
#
#  File Description: API for setting up Rest Session with ILO 
#                    for querying Status of various Server Components
#


import sys
import re
import shlex
import subprocess
import string
import os
import os.path
from multiprocessing import Pool
import json
import argparse
import logging
import datetime as dt
import collections

from clinfo import OpenCL

def _get_smbios_data():
    """Collects the PCI slot designation and bus address from DMI.
    
    Runs dmidecode and parses the following data from the command ouput:
        Designation: PCI-E Slot 3
        Bus Address: 0000:09:00.0
    
    Args: None
    Returns:
        A dict that maps a pci device (key) to the slot designation and pci address.
        {'0000:0d:00.0': { 'location': { 'slot': 'PCI-E Slot 1',
                                          'bus': '0000:0d:00.0' } },
         '0000:0e:00.0': { 'location': { 'slot': 'PCI-E Slot 2', 
                                          'bus': '0000:0e:00.0' } }}
    """

    # Example command output
    """
    # dmidecode 2.12
    SMBIOS 2.8 present.

    Handle 0x0903, DMI type 9, 17 bytes
    System Slot Information
        Designation: PCI-E Slot 3
        Type: x16 PCI Express 3 x16
        Current Usage: In Use
        Length: Long
        ID: 3
        Characteristics:
                3.3 V is provided
                PME signal is supported
        Bus Address: 0000:09:00.0
    """

    cmd = '/usr/sbin/dmidecode -t slot'
    logging.info(cmd)
    output = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE).communicate()[0]
    result = dict()
    matchobj = re.findall('Handle.*?\n\n', output, re.MULTILINE|re.DOTALL)
    for pcidevice in matchobj:
        matchobj = re.search('Designation: (.*)', pcidevice)
        (slot) = matchobj.group(1)
        matchobj = re.search('Bus Address: ([\d\w]+):([\d\w]+):([\d\w]+)\.([\d\w]+)', pcidevice)
        (domain, bus, device, function) = matchobj.group(1, 2, 3, 4)
        device_id = "%s:%s:%s.%s" % (domain, bus, device, function)
        result[device_id] = { 'location': { 'slot': slot, 'bus': device_id } }

        logging.debug("[smbios] %s: %s:%s:%s.%s" % (slot, domain, bus, device, function))
    return result

def _get_aticonfig_data():
    """Collects the product name for each GPU in the system.

    Runs the aticonfig command and parses the product name
    and bus address from the output.

    Args: None
    Returns:
        A dict that maps a pci device (key) to the device name.
        {'0000:0e:00.0': { 'product': { 'name': 'AMD FirePro S9150 (FireGL V)'}}, 
         '0000:33:00.0': { 'product': { 'name': 'AMD FirePro S9150 (FireGL V)'}}
    """

    cmd = '/usr/bin/aticonfig --list-adapters'
    logging.info(cmd)
    output = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE).communicate()[0]
    result = dict()
    ## What is this regex doing? 
    # Command: "/usr/bin/aticonfig --list-adapters"
    # String: " * 0. 0e:00.0 AMD FirePro S9150 (FireGL V)"
    #   id = 0
    #   bus = 0e (hex), device = 00 (hex), function = 0 (in BDF notation)
    #   product name = "AMD FirePro S9150 (FireGL V)"
    regex = '\*?\s+(\d+)\.\s+([\d\w]{2}):([\d\w]{2})\.(\d)\s+(.*)'

    matchobj = re.findall(regex, output)
    for gpudevice in matchobj:
        (id, bus, device, function, productname) = gpudevice
        device_id = "%s:%s:%s.%s" % ("0000", bus, device, function)
        result[device_id] = { 'product': { 'name': productname} }
        logging.debug("[aticonfig] %s: %s:%s.%s %s" % (id, bus, device, function, productname))
    return result

def _get_agi_adapter_list():
    """Collects the AMD utility instance id for each GPU in the system.

    Runs the agi command and parses the instance id and bus address
    from the output. The instance id is used with 'agi -i=id' to collect
    data from a specific gpu.

    Args: None
    Returns:
        A dict that maps a pci device (key) to the AMD utility instance id.
        {'0000:09:00.0': {'instanceId': 0},
         '0000:0a:00.0': {'instanceId': 1}}
    """

    cmd = '/usr/bin/agi -i'
    logging.info(cmd)
    output = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE).communicate()[0]
    result = dict()

    ## What is this regex doing?
    # Command: "/usr/bin/atitool -i"
    # String: "       0  VendorID: 0x1002 DeviceID: 0x67a0 SSID: 0x00 (0a:00.0) (C68103-FGL HAWAII)"
    #   instance = 0
    #   bus = 0a (hex), device = 00 (hex), function = 0 (in BDF notation)
    regex = '\n\s*(\d).*\(([\d\w]{2}):([\d\w]{2})\.(\d)\)'

    matchobj = re.findall(regex, output)
    for gpudevice in matchobj:
        (instanceid, bus, device, function) = gpudevice
        device_id = "%s:%s:%s.%s" % ("0000", bus.lower(), device.lower(), function)
        result[device_id] = { 'instanceId': int(instanceid) }
        logging.debug("[agi] %s: %s:%s.%s" % (instanceid, bus, device, function))
    return result

def _get_opencl_devices():
    """Collects the opencl data for each GPU in the system.

    Queries each GPU device available in opencl and collects the
    available information. Uses the python OpenCL class from clinfo.py,
    which utilizes the AMD vendor extensions for bus topology information.

    Args: None
    Returns:
        A dict that maps a pci device (key) to the data available from opencl.
        {'0000:0e:00.0': {'opencl': {'available': 'Yes',
                                     'vendor_name': 'Advanced Micro Devices, Inc.',
                                     'device_type': 4,
                                     'device_name': 'Hawaii',
                                     'vendor_id': 4098,
                                     'device_id': 12695952L,
                                     'topology': { 'device': 0,
                                                   'bus': 14} }}}
    """

    result = dict()
    opencl = OpenCL()
    ocl_gpu_devices = opencl.getGPUDevices()
    for gpu in ocl_gpu_devices:
        device_id = "%s:%02x:%02x.%d" % ("0000", gpu['topology']['bus'], gpu['topology']['device'], 0)
        result[device_id] = {'opencl': dict(gpu)}
        logging.info("[opencl] Found gpu device [%02x:%02x]" % (gpu['topology']['bus'], gpu['topology']['device']))
    return result

def _get_sysfs_pci_info(deviceList):
    """Collects the vendor and device id for a specified PCI device
        in the system.

    Reads the vendor and device identification information available
    in the PCI configuration space for a specific PCI device. The data
    is read from the sysfs virtual filesystem.

    Args:
        deviceList: a collection of strings that represent a PCI bus address
    Returns:
        A dict that maps a pci device (key) to vendor/device id 
        {'0000:0e:00.0': {'hardwareId': {'vendor': '0x1002',
                                         'device': '0x67a0',
                                         'subsystemVendor': '0x1002',
                                         'subsystemDevice': '0x0335' }}}
    """

    vendor_id="/sys/bus/pci/devices/%s/vendor"
    device_id="/sys/bus/pci/devices/%s/device"
    subsystem_vendor_id="/sys/bus/pci/devices/%s/subsystem_vendor"
    subsystem_device_id="/sys/bus/pci/devices/%s/subsystem_device"

    result = dict()
    for bus_id in deviceList:
        pci_data={}

        if not os.path.isdir("/sys/bus/pci/devices/%s" % bus_id):
            continue
        else:
            with open(vendor_id % bus_id) as f:
                logging.info('Reading file: %s' % f.name)
                pci_data['vendor'] = f.read().strip()
            with open(device_id % bus_id) as f:
                logging.info('Reading file: %s' % f.name)
                pci_data['device'] = f.read().strip()
            with open(subsystem_vendor_id % bus_id) as f:
                logging.info('Reading file: %s' % f.name)
                pci_data['subsystemVendor'] = f.read().strip()
            with open(subsystem_device_id % bus_id) as f:
                logging.info('Reading file: %s' % f.name)
                pci_data['subsystemDevice'] = f.read().strip()
            result[bus_id] = {'hardwareId': pci_data}
    
    return result

def _get_proc_node_busid(node_num):
    """Gets the PCI bus address for a AMD GPU device node.

    Reads the PCI bus address for a given node in the AMD GPU driver
    procfs directory. A node for each GPU is present in the /proc/ati/
    directory and contains the bios version and client (process) list
    for the AMD driver.

    Args:
        node_num: a integer representing the node number of GPU device
    Returns:
        A string representation of a pci bus address.
        Ex. "0000:0d:00.0"
    """
    gpu_node = "/proc/ati/%s" % node_num
    if os.path.exists(gpu_node):
        file_name = gpu_node + "/interrupt_info"
        logging.info("Reading file %s" % file_name)
        f = open(file_name, 'r')
        output = f.read()
        logging.debug(file_name + os.linesep + output)
        matchobj = re.search("PCI:(\d{1,2}):(\d{1,2}):(\d)", output)
        if matchobj is None:
            return None
        (bus, device, function) = matchobj.group(1, 2, 3)
        return "%04x:%02x:%02x.%x" % (0, int(bus), int(device), int(function))
    else:
        return None

def _get_proc_clients(devices=range(16)):
    """Collects the list of clients attached to the GPU device driver.

    Reads the list of processes (clients) that are using a specific GPU device
    from the /proc/ati/[n]/clients file. This file is updated by the AMD device
    driver.

    Args:
        devices: an array of device nodes to search
    Returns:
        A dict that maps a pci device (key) to an array of clients that are
        using the gpu device.
        {'0000:0e:00.0': {'status': {'clients':
                               [ {'pid': 54868,
                                  'filename': '/usr/release/rec'} ] }}}
    """

    result = dict()
    try:
        device_iterator = iter(devices)
    except TypeError:
        return None
    self_pid = os.getpid()
    for device_number in device_iterator:
        gpu_node = "/proc/ati/%s" % device_number
        if os.path.exists(gpu_node):
            client_list = list()
            bus_id = _get_proc_node_busid(device_number)
            file_name = gpu_node + "/clients"
            logging.info("Reading file %s" % file_name)
            f = open(file_name, 'r')
            output = f.read()
            logging.debug(file_name + os.linesep + output)
            for client_row in output.splitlines()[2:]:
                client_pid = int(client_row.split()[2])
                if client_pid == self_pid:
                    # Don't add myself to the list
                    continue
                client_executable = os.readlink("/proc/%s/exe" % client_pid)
                client_list.append({'pid': client_pid, 'filename': client_executable})
            f.close()
            result[bus_id] = { 'status': {'clients': client_list} }
    return result

def _get_proc_vbiosversion(devices=range(16)):
    """Collects the vbios version for an AMD GPU device.

    Reads the vbios version for a specific GPU device from the
    /proc/ati/[n]/biosversion file. This file is updated by the AMD device driver.

    Args:
        devices: an array of device nodes to search
    Returns:
        A dict that maps a AMD GPU pci device (key) to it's vbios version.
        {'0000:0e:00.0': {'product': {'biosPartNumber': '113-C6810300-102'}},
         '0000:0d:00.0': {'product': {'biosPartNumber': '113-C6810300-102'}}}
    """

    result = dict()
    try:
        device_iterator = iter(devices)
    except TypeError:
        return None
    for device_number in device_iterator:
        gpu_node = "/proc/ati/%s" % device_number
        if os.path.exists(gpu_node):
            bus_id = _get_proc_node_busid(device_number)
            data = dict()

            file_name = gpu_node + "/biosversion"
            logging.info("Reading file %s" % file_name)
            with open(file_name, 'r') as file:
                output = file.read()
                logging.debug(os.linesep + output)
                for row in output.splitlines()[1:]:
                    matchobj = re.match('(.+)=\"(.*)\"', row)
                    if matchobj is not None:
                        attribute, value = matchobj.group(1), matchobj.group(2)
                        data[attribute] = value.strip()

            if 'BIOS_PN' in data:
                result[bus_id] = { 'product': {'biosPartNumber': data['BIOS_PN']} }
            else:
		logging.warn('Unable to retrieve vbios version from %s' % file_name)
    return result

def _get_agi_gpustatus(device=None):
    """Collects the gpu status data from the specified GPU device.

    Run the AMD GPU utility (agi) to collect the gpu status information
    from a GPU device or all devices if no device is specified. 

    Args:
        device: a string representation of a pci bus address
    Returns:
        A dict that maps a GPU device (key) to the usage status information
        {'0000:0e:00.0': {
             'status': {
                 'usage': {
                     'PCIE address': '14:00.0',
                     'PCIE Link width': 'x16',
                     'Calculated GPU Power': '49.05 W',
                     'Memory activity level': '0.00 %',
                     'PCIE Link speed': '2.5 GT/s',
                     'ASIC Temperature': '57.00 C',
                     'GFX activity Level': '0.00 %',
                     'Calculated Board Power': '63.77 W'}
        }}}
    """

    ## Example command output
    """
    AMD GPU Info version 0.2.3.1, Copyright (c) 2015 Advanced Micro Devices, Inc.
    
    
    [0] VendorID: 0x1002 DeviceID: 0x67a0 SSID: 0x0335 (09:00.0) (C68103-FGL HAWAII)
    --------------------------------------------------------------------------------
    
        ASIC Temperature       : 63.00 C
        Calculated GPU Power   : 50.29 W
        Calculated Board Power : 65.38 W
        GFX activity Level     : 0.00 %
        Memory activity level  : 0.00 %
        PCIE address           : 09:00.0
        PCIE Link speed        : 2.5 GT/s
        PCIE Link width        : x16
    """

    data = dict()
    if device is None:
        device_arg = '*'
    else:
        device_arg = 'PCI:' + device 
    cmd = '/usr/bin/agi -i=%s -gpustatus' % device_arg
    logging.info(cmd)
    output = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE).communicate()[0]

    ## What is this regex doing?
    regex = "\[\d+\].*?\((.*?)\) \(.*?\)\n-{80}\n\n(.*?)\n\n"
    # Command: "/usr/bin/agi -i=* -gpustatus"
    # String: "[0] VendorID: 0x1002 DeviceID: 0x67a0 SSID: 0x0335 (09:00.0) (C68103-FGL HAWAII)\n
    #         "--------------------------------------------------------------------------------\n
    #         "\n
    #         "    ASIC Temperature       : 59.00 C\n
    #         "    Calculated GPU Power   : 49.18 W\n
    #         "    Calculated Board Power : 63.94 W\n
    #         "    GFX activity Level     : 0.00 %\n
    #         "    Memory activity level  : 0.00 %\n
    #         "    PCIE address           : 09:00.0\n
    #         "    PCIE Link speed        : 2.5 GT/s\n
    #         "    PCIE Link width        : x16\n
    #         "\n
    #         "\n"
    #
    #   bus_id = 09:00.0
    #   attributes = ['ASIC Temperature', 'Calculated GPU Power', ...]
    
    matchobj = re.findall(regex, output, re.MULTILINE|re.DOTALL)
    for gpudevice in matchobj:
        (bus_id, attributes) = gpudevice
        bus_id = '0000:' + bus_id
        data[bus_id] = {'status': { 'usage': {} }}
        for attribute in attributes.splitlines():
             (key, value) = map(string.strip, attribute.split(':', 1))
             data[bus_id]['status']['usage'][key] = value

    return data

def _get_agi_serial_id(device=None):
    """Collects the gpu serial number from the specified GPU device.

    Runs the AMD GPU utility (agi) to collect the serial number
    from a GPU device or all devices if no device is specified.

    Args:
        device: a string representation of a pci bus address
    Returns:
        A dict that maps a GPU device (key) to the usage status information
        {'0000:0e:00.0': {'product': {'serialNumber': '0000008e28430303000001af561374cb'}},
         '0000:33:00.0': {'product': {'serialNumber': '0000008e28430305000001ca561374cb'}}}
    """

    data = dict()
    if device is None:
        device_arg = '*'
    else:
        device_arg = 'PCI:' + device
    cmd = '/usr/bin/agi -i=%s -sid' % device_arg
    logging.info(cmd)
    output = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE).communicate()[0]

    ## What is this regex doing?
    regex = "\[\d+\].*?\((.*?)\) \(.*?\)\n-{80}\n\nSerialID Data = ([0-9a-z]*)\n"
    # Command: "/usr/bin/agi -i=* -sid"
    # String: "[0] VendorID: 0x1002 DeviceID: 0x67a0 SSID: 0x0335 (09:00.0) (C68103-FGL HAWAII)\n
    #         "--------------------------------------------------------------------------------
    #         "\n
    #         "SerialID Data = 0000008e28430302000000ee561374cb\n"
    #
    #   bus_id = 09:00.0
    #   serialnum = 0000008e28430302000000ee561374cb

    # simplified regex that only captures the serial number
    # used only if a device arg is specified
    regex_single_dev = "SerialID Data = ([0-9a-z]*)\n"

    if device is None:
        matchobj = re.findall(regex, output, re.MULTILINE|re.DOTALL)
        for gpudevice in matchobj:
            (bus_id, serialnum) = gpudevice
            bus_id = '0000:' + bus_id
            data[bus_id] = {'product': { 'serialNumber': serialnum }}
    else:
        matchobj = re.findall(regex_single_dev, output, re.MULTILINE|re.DOTALL)
        for gpudevice in matchobj:
            (serialnum) = gpudevice
            data[device] = {'product': { 'serialNumber': serialnum }}
    return data

def update_dict_recursive(data, update):
    """Performs a recursive update of two dictionary structures.

    Updates a dictionary structure (var: data) with the changes in var: update.
    The values from the update dict are added to the data dict or updated if they
    already exist.
    This function differs from the builtin dict update function, in that nth level
    nested dictionary items are also updated instead of being overwritten.

    Args:
        data: a dict object that will be updated.
        update: a dict object whose values will update the data dict
    Returns:
        the data dict that has been updated.
    """

    for key, value in update.iteritems():
        if isinstance(value, collections.Mapping):
            r = update_dict_recursive(data.get(key, {}), value)
            data[key] = r
        else:
            data[key] = update[key]
    return data

def callback_func(data):
    """Callback function to update the global pci data dictionary.

    This function is called asynchronously by the run_async worker threads.
    It updates the global pci_data dictionary with the data return from the
    function.

    Args:
        data: the data returned from the async function
    Returns: None
    """
    global pci_data
    pci_data = update_dict_recursive(pci_data, data)

pci_data = dict()
def main():
    start_time = dt.datetime.now()

    parser = argparse.ArgumentParser(description='Display AMD GPU information.')
    parser.add_argument('-s', '--short', dest='abridge', action='store_true', help='Skip extended diagnostic tests.')
    parser.add_argument('-d', '--device', dest='device', action='store', help='Choose device (bus id) to display data for.')
    parser.add_argument('--logfile', dest='logfile', action='store', metavar='filename', help='Log file location.')
    parser.add_argument('--debug', dest='debug', action='store_true', help="Print additional debug messages.")
    args = parser.parse_args()

    if args.logfile:
        log_level = logging.DEBUG if args.debug else logging.INFO
        logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s', level=log_level, filename=args.logfile, datefmt='%m/%d/%Y %I:%M:%S %p')
        logging.info('Script Main')
        logging.info(args)
    else:
        log_level = logging.CRITICAL
        logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s', level=log_level, datefmt='%m/%d/%Y %I:%M:%S %p') 

    num_threads = 1
    logging.debug("Creating threadpool with %d thread(s)." % num_threads)
    threadpool = Pool(processes=num_threads)
    thread_results = list()

    def run_async(func, timeout, args, callback):
        async_result = threadpool.apply_async(func, args, callback=callback)
        thread_results.append( (func, async_result, timeout) )

    run_async(_get_smbios_data, 5, (), callback_func)
    run_async(_get_aticonfig_data, 5, (), callback_func)
    run_async(_get_proc_clients, 5, (), callback_func)
    run_async(_get_proc_vbiosversion, 5, (), callback_func)
    run_async(_get_sysfs_pci_info, 5, (_get_smbios_data().keys(),), callback_func)

    if not args.abridge:
        if args.device:
            run_async(_get_agi_gpustatus, 8, (args.device,), callback_func)
            run_async(_get_agi_serial_id, 8, (args.device,), callback_func)
        else:
            run_async(_get_agi_gpustatus, 8, (), callback_func)
            run_async(_get_agi_serial_id, 8, (), callback_func)


    for item in thread_results:
        (func, async_result, wait_time) = item
        if async_result.ready() and async_result.successful():
            continue
        logging.info("Waiting for function " + func.__name__)
        async_result.wait(wait_time)
        if async_result.ready():
            if not async_result.successful():
                logging.warn("Function[%s] did not complete due to unhandled exception." % (func.__name__))
        else:
            logging.warn("Function[%s] did not return within %s seconds." % (func.__name__, wait_time))
    
    threadpool.close()
    threadpool.terminate()
    #threadpool.join()

    gpu_data = { 'gpuDevices': list() }
    pci_data_sorted = sorted(pci_data.keys(), key=lambda busid: pci_data[busid]['location']['slot'])
    pci_data_filtered = filter(lambda d: 'product' in pci_data[d], pci_data_sorted)
    if args.device:
        pci_data_filtered = filter(lambda d: d == args.device, pci_data_sorted)

    for busid in pci_data_filtered:
        gpu_data['gpuDevices'].append( pci_data[busid] )

    json_data = json.dumps(gpu_data, sort_keys=False, indent=4)
    print json_data
    logging.info("Returning JSON Data:" + os.linesep + json_data)

    finish_time = dt.datetime.now()
    run_time = finish_time - start_time

    rc = 0
    logging.info("Returning " + str(rc) + ", Execution Time: " + str(run_time))
    return rc

if __name__ == "__main__":
    rc = main()
    exit(rc)
