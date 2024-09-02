#!/usr/bin/python

from ctypes import *
import sys

CL_DEVICE_TOPOLOGY_TYPE_PCIE_AMD = 1
class cl_device_topology_amd(Structure):
     _fields_ = [('type', c_uint),
                ('unused', c_char * 17),
                ('bus', c_char),
                ('device', c_char),
                ('function', c_char)]

class OpenCL():
    library = CDLL('libOpenCL.so')

    CL_DEVICE_VENDOR_ID_AMD        = 0x1002
    CL_DEVICE_VENDOR_ID_NVIDIA     = 0x10DE

    # cl_device_type - bitfield
    CL_DEVICE_TYPE_DEFAULT         = 1 << 0
    CL_DEVICE_TYPE_CPU             = 1 << 1
    CL_DEVICE_TYPE_GPU             = 1 << 2
    CL_DEVICE_TYPE_ACCELERATOR     = 1 << 3
    CL_DEVICE_TYPE_ALL             = 0xFFFFFFFF

    # cl_device_info
    CL_DEVICE_TYPE                 = 0x1000
    CL_DEVICE_VENDOR_ID            = 0x1001
    CL_DEVICE_NAME                 = 0x102B
    CL_DEVICE_VENDOR               = 0x102C
    CL_DEVICE_AVAILABLE            = 0x1027

    # cl_nv_device_attribute_query extension
    CL_DEVICE_PCI_BUS_ID_NV        = 0x4008
    CL_DEVICE_PCI_SLOT_ID_NV       = 0x4009

    # cl_amd_device_attribute_query extension
    CL_DEVICE_TOPOLOGY_AMD         = 0x4037

    # Error Codes
    CL_SUCCESS                     = 0
    CL_DEVICE_NOT_FOUND            = -1
    CL_DEVICE_NOT_AVAILABLE        = -2

    @classmethod
    def getPlatformCount(cls):
        platformCount = c_int(0)
        cls.library.clGetPlatformIDs(c_int(0), None, byref(platformCount))
        return platformCount.value 

    @classmethod
    def getPlatformIDs(cls):
        platformCount = cls.getPlatformCount()
        platformIds = (c_ulong * platformCount)()
        cls.library.clGetPlatformIDs(platformCount, byref(platformIds), None)
        return platformIds

    @classmethod
    def getAllDevices(cls, platformId=None):
        return cls.getDeviceByType(platformId=platformId, deviceType=OpenCL.CL_DEVICE_TYPE_ALL)

    @classmethod
    def getGPUDevices(cls, platformId=None):
        return cls.getDeviceByType(platformId=platformId, deviceType=OpenCL.CL_DEVICE_TYPE_GPU)

    @classmethod
    def getDeviceByType(cls, platformId=None, deviceType=CL_DEVICE_TYPE_ALL):
        devices = []
        platformIds = platformId
        if platformId is None:
            platformIds = cls.getPlatformIDs()

        for platform_id in platformIds:
            deviceCount = c_int(0)
            err = cls.library.clGetDeviceIDs(c_ulong(platform_id), deviceType, 0, None, byref(deviceCount))
            deviceIDs = (c_ulong * deviceCount.value)()
            err = cls.library.clGetDeviceIDs(c_ulong(platform_id), deviceType, deviceCount, byref(deviceIDs), None)
            for device_id in deviceIDs:
                devices.append(OpenCL.OCLDevice.create_device(device_id))
        return devices 

    class OCLDevice(dict):
        def __init__(self, device_id, device_type):
            self['device_id'] = device_id
            self['device_type'] = device_type

            ## Get CL_DEVICE_VENDOR_ID Attribute
            vendor_id = c_int(0)
            OpenCL.library.clGetDeviceInfo.restype = c_int
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_VENDOR_ID, sizeof(vendor_id), byref(vendor_id), None)
            self['vendor_id'] = vendor_id.value

            ## Get CL_DEVICE_VENDOR Attribute
            ret_size = c_int(0)
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_VENDOR, 0, None, byref(ret_size))
            vendor_name = create_string_buffer(ret_size.value)
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_VENDOR, ret_size, vendor_name, None)
            self['vendor_name'] = vendor_name.value

            ## Get CL_DEVICE_NAME Attribute
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_NAME, 0, None, byref(ret_size))
            device_name = create_string_buffer(ret_size.value)
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_NAME, ret_size, device_name, None)
            self['device_name'] = device_name.value

            ## Get CL_DEVICE_AVAILABLE Attribute
            device_available = c_uint(0)
            OpenCL.library.clGetDeviceInfo.restype = c_uint
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_AVAILABLE, sizeof(device_available), byref(device_available), None)
            self['available'] = "Yes" if device_available.value else "No"

        @staticmethod
        def create_device(device_id):
            mapping = { OpenCL.CL_DEVICE_TYPE_CPU: OpenCL.CPUDevice,
                        OpenCL.CL_DEVICE_TYPE_GPU: OpenCL.GPUDevice }
            device_type = c_ulong(0)
            OpenCL.library.clGetDeviceInfo.restype = c_ulong
            OpenCL.library.clGetDeviceInfo(c_ulong(device_id), OpenCL.CL_DEVICE_TYPE, sizeof(device_type), byref(device_type), None)

            if mapping.has_key(device_type.value):
                return mapping[device_type.value](device_id)
            else:
                return OpenCL.OCLDevice(device_id, device_type)

    class CPUDevice(OCLDevice):
        def __init__(self, device_id):
            OpenCL.OCLDevice.__init__(self, device_id, OpenCL.CL_DEVICE_TYPE_CPU)
    class GPUDevice(OCLDevice):
        def __init__(self, device_id):
            OpenCL.OCLDevice.__init__(self, device_id, OpenCL.CL_DEVICE_TYPE_GPU)
            topology = self._getTopology()
            if topology is not None:
                self['topology'] = topology

        def _getTopology(self):
            if self['vendor_id'] == OpenCL.CL_DEVICE_VENDOR_ID_AMD:
                topology = cl_device_topology_amd()
                status = c_int(0)
                OpenCL.library.clGetDeviceInfo.restype = c_int
                status = OpenCL.library.clGetDeviceInfo(self['device_id'], OpenCL.CL_DEVICE_TOPOLOGY_AMD, sizeof(cl_device_topology_amd), byref(topology), None)
                if topology.type == CL_DEVICE_TOPOLOGY_TYPE_PCIE_AMD:
                    return {'bus': ord(topology.bus), 'device': ord(topology.device)}

            elif self['vendor_id'] == OpenCL.CL_DEVICE_VENDOR_ID_NVIDIA:
                bus = c_uint(0)
                slot = c_uint(0)
                OpenCL.library.clGetDeviceInfo(self['device_id'], OpenCL.CL_DEVICE_PCI_BUS_ID_NV, sizeof(bus), byref(bus), None)
                OpenCL.library.clGetDeviceInfo(self['device_id'], OpenCL.CL_DEVICE_PCI_SLOT_ID_NV, sizeof(slot), byref(slot), None)
                return {'bus': bus.value, 'device': slot.value}

            return None

if __name__ == "__main__":
    opencl = OpenCL()
    print "Number of platforms: %s" % opencl.getPlatformCount()
    for device in opencl.getGPUDevices():
        print device

    sys.exit(0)

