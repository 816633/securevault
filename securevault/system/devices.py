"""设备枚举与探测。

全部通过 ctypes 直调 Win32（kernel32 / setupapi / cfgmgr32），
**不调用** cmd / powershell / wmic，全程静默、不弹窗、失败只返回错误。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

from ..core.model import DeviceInfo, now_rfc3339

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3

FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
STORAGE_DEVICE_PROPERTY = 0
PROPERTY_STANDARD_QUERY = 0

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

SPDRP_DEVICEDESC = 0x0
SPDRP_HARDWAREID = 0x1
SPDRP_FRIENDLYNAME = 0xC
SPDRP_LOCATION_INFORMATION = 0xD
SPDRP_ENUMERATOR_NAME = 0x16

PROBE_TIMEOUT = 2.0
ENUMERATE_TIMEOUT = 3.0


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str = "") -> None:
        super().__init__()
        if not text:
            return
        body = text.strip("{}")
        head, _, tail = body.partition("-")
        parts = tail.split("-")
        self.Data1 = int(head, 16)
        self.Data2 = int(parts[0], 16)
        self.Data3 = int(parts[1], 16)
        raw = parts[2] + parts[3]
        for index in range(8):
            self.Data4[index] = int(raw[index * 2:index * 2 + 2], 16)


GUID_DEVINTERFACE_DISK = GUID("53f56307-b6bf-11d0-94f2-00a0c91efb8b")
GUID_DEVINTERFACE_VOLUME = GUID("53f5630d-b6bf-11d0-94f2-00a0c91efb8b")


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", ctypes.c_uint32),
        ("QueryType", ctypes.c_uint32),
        ("AdditionalParameters", ctypes.c_ubyte * 4),
    ]


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [
        ("DeviceType", ctypes.c_uint32),
        ("DeviceNumber", ctypes.c_uint32),
        ("PartitionNumber", ctypes.c_uint32),
    ]


class DISK_EXTENT(ctypes.Structure):
    _fields_ = [
        ("DiskNumber", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("StartingOffset", ctypes.c_int64),
        ("ExtentLength", ctypes.c_int64),
    ]


class VOLUME_DISK_EXTENTS(ctypes.Structure):
    _fields_ = [
        ("NumberOfDiskExtents", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("Extents", DISK_EXTENT * 8),
    ]


kernel32.GetLogicalDrives.restype = wintypes.DWORD
kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
kernel32.GetDriveTypeW.restype = wintypes.UINT
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                         wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
cfgmgr32.CM_Get_Parent.restype = wintypes.ULONG
cfgmgr32.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                   wintypes.ULONG]
cfgmgr32.CM_Get_Device_IDW.restype = wintypes.ULONG
cfgmgr32.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR,
                                       wintypes.ULONG, wintypes.ULONG]

_DETAIL_CB_SIZE = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


# ---------------------------------------------------------------------------
# 基础探测
# ---------------------------------------------------------------------------

def drive_mask_to_letters(mask: int) -> List[str]:
    return [chr(ord("A") + i) + ":" for i in range(26) if mask & (1 << i)]


def get_logical_drives() -> int:
    return int(kernel32.GetLogicalDrives() or 0)


def get_drive_type(root: str) -> int:
    return int(kernel32.GetDriveTypeW(root))


def volume_information(root: str) -> Tuple[str, str, bool]:
    volume = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD(0)
    max_comp = wintypes.DWORD(0)
    flags = wintypes.DWORD(0)
    ok = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), volume, 261, ctypes.byref(serial),
        ctypes.byref(max_comp), ctypes.byref(flags), fs, 261,
    )
    return (volume.value, fs.value, bool(ok))


def disk_free_space(root: str) -> Tuple[int, int, bool]:
    free_avail = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    total_free = ctypes.c_ulonglong(0)
    ok = kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(root), ctypes.byref(free_avail),
        ctypes.byref(total), ctypes.byref(total_free),
    )
    return int(total.value), int(total_free.value), bool(ok)


def volume_name_for_mount_point(root: str) -> str:
    buf = ctypes.create_unicode_buffer(261)
    ok = kernel32.GetVolumeNameForVolumeMountPointW(ctypes.c_wchar_p(root), buf, 261)
    return buf.value if ok else ""


def open_device_path(path: str) -> Optional[int]:
    handle = kernel32.CreateFileW(
        ctypes.c_wchar_p(path), 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return None
    return int(handle)


def close_handle(handle: int) -> None:
    try:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass


def device_io_control(handle: int, code: int, in_struct, out_struct, out_size: int) -> int:
    """发送 IOCTL。``in_struct`` 可为 None；两个参数都直接传结构体 / 缓冲区对象。"""
    returned = wintypes.DWORD(0)
    in_ptr = ctypes.byref(in_struct) if in_struct is not None else None
    in_size = ctypes.sizeof(in_struct) if in_struct is not None else 0
    ok = kernel32.DeviceIoControl(
        wintypes.HANDLE(handle), code, in_ptr, in_size,
        ctypes.byref(out_struct), out_size,
        ctypes.byref(returned), None,
    )
    if not ok:
        return 0
    return int(returned.value)


def volume_disk_number(handle: int) -> int:
    extents = VOLUME_DISK_EXTENTS()
    size = device_io_control(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, None,
                             extents, ctypes.sizeof(extents))
    if size < 8 or extents.NumberOfDiskExtents < 1:
        return -1
    return int(extents.Extents[0].DiskNumber)


def storage_device_number(handle: int) -> Tuple[int, int]:
    info = STORAGE_DEVICE_NUMBER()
    size = device_io_control(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, None,
                             info, ctypes.sizeof(info))
    if size < ctypes.sizeof(info):
        return -1, -1
    return int(info.DeviceNumber), int(info.PartitionNumber)


def storage_descriptor(handle: int) -> Tuple[int, str, str, str]:
    """返回 (总线类型, 磁盘序列号, 厂商, 产品名)。"""
    query = STORAGE_PROPERTY_QUERY()
    query.PropertyId = STORAGE_DEVICE_PROPERTY
    query.QueryType = PROPERTY_STANDARD_QUERY
    buf = ctypes.create_string_buffer(1024)
    size = device_io_control(handle, IOCTL_STORAGE_QUERY_PROPERTY,
                             query, buf, len(buf))
    if size < 36:
        return 0, "", "", ""
    data = buf.raw[:size]

    def u32(offset: int) -> int:
        if offset < 0 or offset + 4 > len(data):
            return 0
        return int.from_bytes(data[offset:offset + 4], "little")

    def ascii_z(offset: int) -> str:
        if offset <= 0 or offset >= len(data):
            return ""
        end = data.find(b"\x00", offset)
        if end < 0:
            end = len(data)
        return data[offset:end].decode("ascii", "ignore").strip()

    return u32(28), ascii_z(u32(24)), ascii_z(u32(12)), ascii_z(u32(16))


BUS_TYPE_NAMES = {
    0x01: "SCSI", 0x02: "ATAPI", 0x03: "ATA", 0x04: "1394", 0x05: "SSA",
    0x06: "Fibre", 0x07: "USB", 0x08: "RAID", 0x09: "iSCSI", 0x0A: "SAS",
    0x0B: "SATA", 0x0C: "SD", 0x0D: "MMC", 0x0E: "Virtual",
    0x0F: "FileBackedVirtual", 0x10: "Spaces", 0x11: "NVMe", 0x12: "SCM",
    0x13: "UFS",
}


def bus_type_name(value: int) -> str:
    return BUS_TYPE_NAMES.get(value, "Unknown")


# ---------------------------------------------------------------------------
# 设备实例 ID 解析（纯函数，便于自检）
# ---------------------------------------------------------------------------

def instance_id_segments(instance_id: str) -> List[str]:
    text = (instance_id or "").strip()
    if not text:
        return []
    for prefix in ("\\\\?\\", "\\??\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("#", "\\")
    return [part.strip() for part in text.split("\\") if part.strip()]


BUS_PREFIX_RULES = (
    (("USBSTOR", "USB"), "USB"),
    (("SCSI",), "SATA"),
    (("NVME",), "NVMe"),
    (("ATAPI",), "ATAPI"),
    (("IDE",), "ATA"),
    (("ISCSI",), "iSCSI"),
    (("SAS",), "SAS"),
    (("RAID",), "RAID"),
    (("SD",), "SD"),
    (("MMC",), "MMC"),
    (("UFS",), "UFS"),
    (("SCM",), "SCM"),
    (("SPACES",), "Spaces"),
    (("VHD", "VIRTUAL", "FILEBACKEDVIRTUAL"), "Virtual"),
    (("1394",), "1394"),
)


def bus_type_from_instance_id(instance_id: str) -> str:
    segs = instance_id_segments(instance_id)
    if not segs:
        return ""
    first = segs[0].upper()
    for prefixes, name in BUS_PREFIX_RULES:
        if first.startswith(prefixes):
            return name
    return ""


def _hex_after(segment: str, prefix: str) -> str:
    upper = segment.upper()
    index = upper.find(prefix)
    if index < 0:
        return ""
    rest = upper[index + len(prefix):]
    out = []
    for ch in rest[:4]:
        if ch in "0123456789ABCDEF":
            out.append(ch)
        else:
            break
    return "".join(out)


def clean_serial_component(text: str) -> str:
    value = (text or "").strip().strip("&")
    if not value:
        return ""
    if len(value) == 1 and value.isdigit():
        return ""
    if "&" in value:
        head, _, tail = value.rpartition("&")
        if tail.isdigit() and head:
            value = head
    if not value or "&" in value:
        return ""
    return value


def parse_usb_instance_id(instance_id: str) -> Tuple[str, str, str]:
    """从设备实例 ID 解析 (VID, PID, 序列号)。"""
    segs = instance_id_segments(instance_id)
    for index, seg in enumerate(segs):
        vid = _hex_after(seg, "VID_")
        pid = _hex_after(seg, "PID_")
        if not vid or not pid:
            continue
        serial = clean_serial_component(segs[index + 1]) if index + 1 < len(segs) else ""
        return vid, pid, serial
    return "", "", ""


# ---------------------------------------------------------------------------
# SetupAPI 枚举
# ---------------------------------------------------------------------------

class DiskEntry:
    __slots__ = ("device_number", "device_path", "instance_id", "usb_instance_id",
                 "device_desc", "friendly_name", "location", "enumerator",
                 "hardware_ids", "bus_type", "serial", "vendor", "product")

    def __init__(self) -> None:
        self.device_number = -1
        self.device_path = ""
        self.instance_id = ""
        self.usb_instance_id = ""
        self.device_desc = ""
        self.friendly_name = ""
        self.location = ""
        self.enumerator = ""
        self.hardware_ids: List[str] = []
        self.bus_type = 0
        self.serial = ""
        self.vendor = ""
        self.product = ""


def _setup_di_get_class_devs(guid: GUID) -> int:
    handle = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return 0
    return int(handle)


def _interface_detail(dev_info: int, iface: SP_DEVICE_INTERFACE_DATA):
    need = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(
        wintypes.HANDLE(dev_info), ctypes.byref(iface), None, 0,
        ctypes.byref(need), None,
    )
    if not need.value:
        return "", None
    buf = ctypes.create_string_buffer(need.value)
    ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(_DETAIL_CB_SIZE)), 4)
    info = SP_DEVINFO_DATA()
    info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
        wintypes.HANDLE(dev_info), ctypes.byref(iface), buf, need.value,
        ctypes.byref(need), ctypes.byref(info),
    )
    if not ok:
        return "", None
    path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
    return path, info


def _device_instance_id(dev_info: int, info: SP_DEVINFO_DATA) -> str:
    buf = ctypes.create_unicode_buffer(512)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDeviceInstanceIdW(
        wintypes.HANDLE(dev_info), ctypes.byref(info), buf, 512, ctypes.byref(need)
    )
    return buf.value if ok else ""


def _registry_property(dev_info: int, info: SP_DEVINFO_DATA, prop: int, size: int = 2048):
    buf = ctypes.create_string_buffer(size)
    prop_type = wintypes.DWORD(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
        wintypes.HANDLE(dev_info), ctypes.byref(info), prop,
        ctypes.byref(prop_type), buf, size, ctypes.byref(need),
    )
    if not ok:
        return None
    return buf.raw[:need.value or size]


def _registry_string(dev_info: int, info: SP_DEVINFO_DATA, prop: int) -> str:
    raw = _registry_property(dev_info, info, prop, 1024)
    if not raw:
        return ""
    return raw.decode("utf-16-le", "ignore").split("\x00")[0]


def _registry_multi_string(dev_info: int, info: SP_DEVINFO_DATA, prop: int) -> List[str]:
    raw = _registry_property(dev_info, info, prop, 2048)
    if not raw:
        return []
    text = raw.decode("utf-16-le", "ignore")
    out: List[str] = []
    for part in text.split("\x00"):
        if not part:
            break
        out.append(part)
    return out


def _cm_device_id(dev_inst: int) -> str:
    if not dev_inst:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    status = cfgmgr32.CM_Get_Device_IDW(dev_inst, buf, 512, 0)
    return buf.value if status == 0 else ""


def _cm_parent(dev_inst: int) -> int:
    parent = wintypes.DWORD(0)
    status = cfgmgr32.CM_Get_Parent(ctypes.byref(parent), dev_inst, 0)
    return int(parent.value) if status == 0 else 0


def find_usb_ancestor(dev_inst: int) -> str:
    """沿设备树向上找 USB 节点（U 盘真正的 VID/PID/串号在父节点上）。"""
    current = dev_inst
    for _ in range(8):
        if not current:
            return ""
        instance = _cm_device_id(current)
        if not instance:
            return ""
        if instance.upper().startswith("USB\\"):
            return instance
        current = _cm_parent(current)
    return ""


def enumerate_disks() -> List[DiskEntry]:
    """枚举 GUID_DEVINTERFACE_DISK 下的全部物理磁盘。"""
    dev_info = _setup_di_get_class_devs(GUID_DEVINTERFACE_DISK)
    if not dev_info:
        return []
    entries: List[DiskEntry] = []
    try:
        for index in range(512):
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                wintypes.HANDLE(dev_info), None, ctypes.byref(GUID_DEVINTERFACE_DISK),
                index, ctypes.byref(iface),
            ):
                break
            path, info = _interface_detail(dev_info, iface)
            if not path or info is None:
                continue
            entry = DiskEntry()
            entry.device_path = path
            entry.instance_id = _device_instance_id(dev_info, info)
            entry.device_desc = _registry_string(dev_info, info, SPDRP_DEVICEDESC)
            entry.friendly_name = _registry_string(dev_info, info, SPDRP_FRIENDLYNAME)
            entry.location = _registry_string(dev_info, info, SPDRP_LOCATION_INFORMATION)
            entry.enumerator = _registry_string(dev_info, info, SPDRP_ENUMERATOR_NAME)
            entry.hardware_ids = _registry_multi_string(dev_info, info, SPDRP_HARDWAREID)
            entry.usb_instance_id = find_usb_ancestor(info.DevInst)
            handle = open_device_path(path)
            if handle:
                try:
                    number, _partition = storage_device_number(handle)
                    entry.device_number = number
                    bus, serial, vendor, product = storage_descriptor(handle)
                    entry.bus_type, entry.serial = bus, serial
                    entry.vendor, entry.product = vendor, product
                finally:
                    close_handle(handle)
            entries.append(entry)
    except Exception:
        pass
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(wintypes.HANDLE(dev_info))
    return entries


def enumerate_disks_with_timeout(timeout: float = ENUMERATE_TIMEOUT) -> List[DiskEntry]:
    box: Dict[str, List[DiskEntry]] = {}

    def worker() -> None:
        try:
            box["value"] = enumerate_disks()
        except Exception:
            box["value"] = []

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    return box.get("value", [])


def volume_interface_path(device_number: int, partition_number: int) -> str:
    dev_info = _setup_di_get_class_devs(GUID_DEVINTERFACE_VOLUME)
    if not dev_info:
        return ""
    try:
        for index in range(512):
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                wintypes.HANDLE(dev_info), None, ctypes.byref(GUID_DEVINTERFACE_VOLUME),
                index, ctypes.byref(iface),
            ):
                break
            path, _info = _interface_detail(dev_info, iface)
            if not path:
                continue
            handle = open_device_path(path)
            if not handle:
                continue
            try:
                number, partition = storage_device_number(handle)
            finally:
                close_handle(handle)
            if number != device_number:
                continue
            if partition_number >= 0 and partition != partition_number:
                continue
            return path
    except Exception:
        return ""
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(wintypes.HANDLE(dev_info))
    return ""


# ---------------------------------------------------------------------------
# 盘符探测
# ---------------------------------------------------------------------------

def normalize_letter(value: str) -> str:
    text = (value or "").strip()
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if not text:
        return ""
    char = text[0].upper()
    if not ("A" <= char <= "Z"):
        return ""
    if len(text) > 1 and text[1] not in (":", "\\", "/"):
        return ""
    return char + ":"


def probes() -> List[str]:
    """返回当前所有可移动 / 固定盘符（不含光驱、网络盘、RAM 盘）。"""
    out = []
    for letter in drive_mask_to_letters(get_logical_drives()):
        if get_drive_type(letter + "\\") in (DRIVE_REMOVABLE, DRIVE_FIXED):
            out.append(letter)
    return out


def _apply_disk_entry(device: DeviceInfo, entry: DiskEntry) -> None:
    instance = entry.usb_instance_id or entry.instance_id
    if instance:
        device.device_instance = instance
    if entry.device_path:
        device.device_path = entry.device_path
    if device.physical_drive < 0 and entry.device_number >= 0:
        device.physical_drive = entry.device_number
    if not device.model:
        device.model = entry.device_desc or entry.product or entry.friendly_name
    if not device.vendor:
        device.vendor = entry.vendor
    if not device.disk_serial:
        device.disk_serial = entry.serial
    if not device.bus_type or device.bus_type == "Unknown":
        if entry.bus_type:
            device.bus_type = bus_type_name(entry.bus_type)
        elif entry.usb_instance_id:
            device.bus_type = "USB"
        else:
            device.bus_type = bus_type_from_instance_id(entry.instance_id) or device.bus_type
    vid, pid, serial = parse_usb_instance_id(entry.usb_instance_id)
    if vid or pid:
        device.vid, device.pid = vid, pid
        if serial:
            device.usb_serial = serial
    if not device.usb_serial and entry.instance_id.upper().startswith("USBSTOR"):
        segs = instance_id_segments(entry.instance_id)
        if segs:
            device.usb_serial = clean_serial_component(segs[-1])


def probe_volume(letter: str, disks: Optional[List[DiskEntry]],
                 partial: DeviceInfo) -> DeviceInfo:
    root = letter + "\\"
    partial.letter = letter
    partial.is_removable = get_drive_type(root) == DRIVE_REMOVABLE

    name, fs, vol_ok = volume_information(root)
    if vol_ok:
        partial.name, partial.fs = name, fs

    capacity, free, space_ok = disk_free_space(root)
    if space_ok:
        partial.capacity, partial.free = capacity, free

    guid = volume_name_for_mount_point(root)
    if guid:
        partial.volume_guid = guid

    device_number, partition_number, number_ok = -1, -1, False
    handle = open_device_path("\\\\.\\" + letter)
    if handle:
        try:
            disk_number = volume_disk_number(handle)
            if disk_number >= 0:
                partial.physical_drive = disk_number
            device_number, partition_number = storage_device_number(handle)
            number_ok = device_number >= 0
            bus, serial, vendor, product = storage_descriptor(handle)
            if bus:
                partial.bus_type = bus_type_name(bus)
            if serial and not partial.disk_serial:
                partial.disk_serial = serial
            if not partial.vendor:
                partial.vendor = vendor
            if not partial.model:
                partial.model = product
        finally:
            close_handle(handle)

    if number_ok and disks:
        for item in disks:
            if item.device_number == device_number:
                _apply_disk_entry(partial, item)
                break

    if not partial.volume_guid and number_ok:
        path = volume_interface_path(device_number, partition_number)
        if path:
            partial.volume_guid = path

    if (not partial.bus_type or partial.bus_type == "Unknown") and partial.device_instance:
        partial.bus_type = bus_type_from_instance_id(partial.device_instance) or partial.bus_type
    if not partial.bus_type:
        partial.bus_type = "Unknown"
    if not partial.device_path:
        partial.device_path = "\\\\.\\" + letter
    partial.system_time = now_rfc3339()
    return partial


def probe(letter: str, disks: Optional[List[DiskEntry]] = None,
          timeout: float = PROBE_TIMEOUT) -> DeviceInfo:
    """探测单个盘符；超时返回已经拿到的部分信息（绝不阻塞界面）。"""
    normalized = normalize_letter(letter)
    if not normalized:
        return DeviceInfo(physical_drive=-1)
    box: Dict[str, DeviceInfo] = {
        "value": DeviceInfo(letter=normalized, physical_drive=-1,
                            system_time=now_rfc3339())
    }

    def worker() -> None:
        try:
            box["value"] = probe_volume(normalized, disks, DeviceInfo(
                letter=normalized, physical_drive=-1, system_time=now_rfc3339()))
        except Exception:
            pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    return box["value"]


def snapshot(timeout: float = PROBE_TIMEOUT) -> Dict[str, DeviceInfo]:
    """枚举当前所有已挂载的可移动 / 固定卷，返回 ``盘符 -> DeviceInfo``。"""
    disks = enumerate_disks_with_timeout()
    out: Dict[str, DeviceInfo] = {}
    for letter in probes():
        info = probe(letter, disks, timeout)
        if info.name or info.capacity or info.model:
            out[letter] = info
    return out
