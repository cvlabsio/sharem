#!/usr/bin/env python

from __future__ import print_function
from unicorn import *
from unicorn.x86_const import *
from capstone import *
from struct import pack, unpack
from collections import defaultdict
import sys
import json
import pefile
from modules import *
from DLLs.dict_signatures import *
from DLLs.dict2_signatures import *
from DLLs.dict3_w32 import *
from DLLs.dict4_ALL import *
from DLLs.hookAPIs import *
import re
import os
import argparse

programCounter = 0
addrTracker = 0x44100000


CODE_ADDR = 0x42000000
CODE_SIZE = 0x1000

GDT_ADDR = 0x41000000
GDT_LIMIT = 0x1000
GDT_ENTRY_SIZE = 0x8

SEGMENT_ADDR = 0x41010000
SEGMENT_SIZE = 0x4000
TIB_ADDR = 0x41015000
TIB_SIZE = 0x100
PEB_ADDR = 0x41017000
PEB_LIMIT = 0x208

STACK_ADDR = 0x47000000
EXTRA_ADDR = 0x48000000
CONST_ADDR = 0x50000000

export_dict = {}
logged_calls = defaultdict(list)
loggedList = []
logged_types = defaultdict(list)
custom_dict = defaultdict(list)
logged_dlls = []
createdProcesses = []
paramValues = []
network_activity = {}

loadModsFromFile = True
foundDLLAddresses="foundDLLAddresses.txt"    # address for files - later we can provide an option to change this in UI
cleanStackFlag = False
stopProcess = False
outFile = open('emulationLog.txt', 'a')
cleanBytes = 0
prevInstruct = []
expandedDLLsPath = "DLLs\\"

prevInstructs = []
loopInstructs = []
loopCounter = 0
verOut = ""
bVerbose = True


F_GRANULARITY = 0x8
F_PROT_32 = 0x4
F_LONG = 0x2
F_AVAILABLE = 0x1
A_PRESENT = 0x80
A_PRIV_3 = 0x60
A_PRIV_2 = 0x40
A_PRIV_1 = 0x20
A_PRIV_0 = 0x0
A_CODE = 0x10
A_DATA = 0x10
A_TSS = 0x0
A_GATE = 0x0
A_EXEC = 0x8
A_DATA_WRITABLE = 0x2
A_CODE_READABLE = 0x2
A_DIR_CON_BIT = 0x4
S_GDT = 0x0
S_LDT = 0x4
S_PRIV_3 = 0x3
S_PRIV_2 = 0x2
S_PRIV_1 = 0x1
S_PRIV_0 = 0x0

def readRaw(appName):
    f = open(appName, "rb")
    myBinary = f.read()
    f.close()
    return myBinary

def insertIntoBytes(binaryBlob, start, size, value):
    lBinary = list(binaryBlob)
    for x in range (size):
        lBinary.insert(start, value)
    final=bytes(lBinary)
    return final

# This struct can have up to 0x58 total bytes depending on Windows version
class PEB_LDR_DATA32():
    def __init__(self, addr, length, initialized, sshandle):
        self.Addr = addr
        self.Length = length
        self.Initialized = initialized
        self.Sshandle = sshandle
        self.ILO_entry = addr + 0xc
        self.IMO_entry = addr + 0x14
        self.IIO_entry = addr + 0x1c
    def allocate(self, mu, ilo_flink, ilo_blink, imo_flink, imo_blink, iio_flink, iio_blink):
        mu.mem_write(self.Addr, pack("<Q", self.Length))
        mu.mem_write(self.Addr+0x4, pack("<Q", self.Initialized))
        mu.mem_write(self.Addr+0x8, pack("<Q", self.Sshandle))
        mu.mem_write(self.Addr+0xc, pack("<Q", ilo_flink) + pack("<Q", ilo_blink))
        mu.mem_write(self.Addr+0x14, pack("<Q", imo_flink) + pack("<Q", imo_blink))
        mu.mem_write(self.Addr+0x1c, pack("<Q", iio_flink) + pack("<Q", iio_blink))

class LDR_Module32():
    def __init__(self, mu, addr, dll_base, entry_point, reserved, full_dll_name, base_dll_name):
        self.Addr = addr
        self.ILO_entry = addr
        self.IMO_entry = addr + 0x8
        self.IIO_entry = addr + 0x10
        self.DLL_Base = dll_base
        self.Entry_Point = entry_point
        self.Reserved = reserved

        global CONST_ADDR
        full_dll_name = full_dll_name.encode("utf-16-le") + b"\x00"
        mu.mem_write(CONST_ADDR, full_dll_name)
        self.Full_Dll_Name = CONST_ADDR
        CONST_ADDR += len(full_dll_name)

        base_dll_name = base_dll_name.encode("utf-16-le") + b"\x00"
        mu.mem_write(CONST_ADDR, base_dll_name)
        self.Base_Dll_Name = CONST_ADDR
        CONST_ADDR += len(base_dll_name)

    def allocate(self, mu, ilo_flink, ilo_blink, imo_flink, imo_blink, iio_flink, iio_blink):
        mu.mem_write(self.Addr, pack("<Q", ilo_flink) + pack("<Q", ilo_blink))
        mu.mem_write(self.Addr+0x8, pack("<Q", imo_flink) + pack("<Q", imo_blink))
        mu.mem_write(self.Addr+0x10, pack("<Q", iio_flink) + pack("<Q", iio_blink))
        mu.mem_write(self.Addr+0x18, pack("<Q", self.DLL_Base))
        mu.mem_write(self.Addr+0x1c, pack("<Q", self.Entry_Point))

        mu.mem_write(self.Addr+0x24, pack("<Q", 0x007e007c))
        mu.mem_write(self.Addr+0x28, pack("<Q", self.Full_Dll_Name))
        mu.mem_write(self.Addr+0x2c, pack("<Q", 0x001c001a))
        mu.mem_write(self.Addr+0x30, pack("<Q", self.Base_Dll_Name))


        pointer = unpack("<I", mu.mem_read(self.Addr+0x30, 4))[0]

class PEB_LDR_DATA64():
    def __init__(self, addr, length, initialized, sshandle):
        self.Addr = addr
        self.Length = length
        self.Initialized = initialized
        self.Sshandle = sshandle
        self.ILO_entry = addr + 0x10
        self.IMO_entry = addr + 0x20
        self.IIO_entry = addr + 0x30
    def allocate(self, mu, ilo_flink, ilo_blink, imo_flink, imo_blink, iio_flink, iio_blink):
        mu.mem_write(self.Addr, pack("<Q", self.Length))
        mu.mem_write(self.Addr+0x4, pack("<Q", self.Initialized))
        mu.mem_write(self.Addr+0x8, pack("<Q", self.Sshandle))
        mu.mem_write(self.Addr+0x10, pack("<Q", ilo_flink) + pack("<Q", ilo_blink))
        mu.mem_write(self.Addr+0x20, pack("<Q", imo_flink) + pack("<Q", imo_blink))
        mu.mem_write(self.Addr+0x30, pack("<Q", iio_flink) + pack("<Q", iio_blink))

class LDR_Module64():
    def __init__(self, addr, dll_base, entry_point, reserved, full_dll_name, base_dll_name):
        self.Addr = addr
        self.ILO_entry = addr
        self.IMO_entry = addr + 0x10
        self.IIO_entry = addr + 0x20
        self.DLL_Base = dll_base
        self.Entry_Point = entry_point
        self.Reserved = reserved
        self.Full_Dll_Name = full_dll_name
        self.Base_Dll_Name = base_dll_name
    def allocate(self, mu, ilo_flink, ilo_blink, imo_flink, imo_blink, iio_flink, iio_blink):
        mu.mem_write(self.Addr, pack("<Q", ilo_flink) + pack("<Q", ilo_blink))
        mu.mem_write(self.Addr+0x10, pack("<Q", imo_flink) + pack("<Q", imo_blink))
        mu.mem_write(self.Addr+0x20, pack("<Q", iio_flink) + pack("<Q", iio_blink))
        mu.mem_write(self.Addr+0x30, pack("<Q", self.DLL_Base))
        mu.mem_write(self.Addr+0x40, pack("<Q", self.Entry_Point))
        mu.mem_write(self.Addr+0x50, pack("<Q", self.Reserved))
        mu.mem_write(self.Addr+0x60, pack("<Q", self.Full_Dll_Name))
        mu.mem_write(self.Addr+0x70, pack("<Q", self.Base_Dll_Name))

def config_GDT(mu, code):
    # Create the GDT entries
    gdt = [buildEntry(0,0,0,0) for i in range(31)]
    gdt[2] = buildEntry(0x0, 0xfffff000, A_PRESENT | A_DATA | A_DATA_WRITABLE | A_PRIV_0, F_PROT_32)
    gdt[15] = buildEntry(TIB_ADDR, TIB_SIZE, A_PRESENT | A_DATA | A_DATA_WRITABLE | A_PRIV_3 | A_DIR_CON_BIT, F_PROT_32)

    buildGdt(mu, gdt, GDT_ADDR)

    # Fill the GDTR register
    mu.reg_write(UC_X86_REG_GDTR, (0, GDT_ADDR, len(gdt)*GDT_ENTRY_SIZE-1, 0x0))

    # Set the FS Register
    selector = buildSegmentSelector(15, S_GDT | S_PRIV_3)
    mu.reg_write(UC_X86_REG_FS, selector)

    # Set the SS Register
    selector = buildSegmentSelector(2, S_GDT | S_PRIV_0)
    mu.reg_write(UC_X86_REG_SS, selector)

def allocateWinStructs32(mu):
    # Put location of PEB at FS:30

    mu.mem_write((PEB_ADDR-10), b'\x4a\x41\x43\x4f\x42\x41\x41\x41\x41\x42')

    mu.mem_write(TIB_ADDR, b'\x00\x00\x00' + b'\x90'*0x2d + pack("<Q", PEB_ADDR))

    # Create PEB data structure. Put pointer to ldr at offset 0xC
    mu.mem_write(PEB_ADDR, b'\x90'*0xc + pack("<Q", LDR_ADDR) + b'\x90'*0x1fc)

    # Create PEB_LDR_DATA structure
    peb_ldr = PEB_LDR_DATA32(LDR_ADDR, 0x24, 0x00000000, 0x00000000)

    dlls_obj = [0]*21

    # Create ldr modules for the rest of the DLLs
    dlls_obj[0] = LDR_Module32(mu, LDR_PROG_ADDR, PROCESS_BASE, PROCESS_BASE, 0x00000000, "C:\\shellcode.exe", "shellcode.exe")

    i = 1
    for dll in allDlls:
        dlls_obj[i] = LDR_Module32(mu, mods[dll].ldrAddr, mods[dll].base, mods[dll].base, 0x00000000, mods[dll].d32, mods[dll].name)
        i += 1

    peb_ldr.allocate(mu, dlls_obj[0].ILO_entry, dlls_obj[20].ILO_entry, dlls_obj[0].IMO_entry, dlls_obj[20].IMO_entry, dlls_obj[1].IIO_entry, dlls_obj[20].IIO_entry)

    # Allocate the record in memory for program, ntdll, and kernel32
    for i in range(0, len(dlls_obj)):
        currentDLL = dlls_obj[i]

        if i == 0:
            nextDLL = dlls_obj[i+1]
            currentDLL.allocate(mu, nextDLL.ILO_entry, dlls_obj[20].ILO_entry, nextDLL.IMO_entry, dlls_obj[20].IMO_entry, nextDLL.IIO_entry, dlls_obj[20].IIO_entry)
        elif i == 20:
            prevDLL = dlls_obj[i-1]
            currentDLL.allocate(mu, dlls_obj[0].ILO_entry, prevDLL.ILO_entry, dlls_obj[0].IMO_entry, prevDLL.IMO_entry, dlls_obj[1].IIO_entry, prevDLL.IIO_entry)
        else:
            nextDLL = dlls_obj[i+1]
            prevDLL = dlls_obj[i-1]
            currentDLL.allocate(mu, nextDLL.ILO_entry, prevDLL.ILO_entry, nextDLL.IMO_entry, prevDLL.IMO_entry, nextDLL.IIO_entry, prevDLL.IIO_entry)

def allocateWinStructs64(mu):
    # Put location of PEB at GS:60
    mu.mem_write(TIB_ADDR, b'\x00'*0x60 + pack("<Q", PEB_ADDR))

    # Create PEB data structure. Put pointer to ldr at offset 0x18
    mu.mem_write(PEB_ADDR, b'\x00'*0x18 + pack("<Q", LDR_ADDR) + b'\x00'*0x1fc)

    # Create PEB_LDR_DATA structure
    peb_ldr = PEB_LDR_DATA64(LDR_ADDR, 0x24, 0x00000000, 0x00000000)
    process = LDR_Module64(LDR_PROG_ADDR, PROCESS_BASE, PROCESS_BASE, 0x00000000, 0x00000000, 0x00000000)
    ntdll = LDR_Module64(LDR_NTDLL_ADDR, NTDLL_BASE, NTDLL_BASE, 0x00000000, 0x00000000, 0x00000000)
    kernel32 = LDR_Module64(LDR_KERNEL32_ADDR, KERNEL32_BASE, KERNEL32_BASE, 0x00000000, 0x00000000, 0x00000000)

    peb_ldr.allocate(mu, process.ILO_entry, kernel32.ILO_entry, process.IMO_entry, kernel32.IMO_entry, ntdll.IIO_entry, kernel32.IIO_entry)
    process.allocate(mu, ntdll.ILO_entry, peb_ldr.ILO_entry, ntdll.IMO_entry, peb_ldr.IMO_entry, 0x00000000, 0x00000000)
    ntdll.allocate(mu, kernel32.ILO_entry, process.ILO_entry, kernel32.IMO_entry, process.IMO_entry, kernel32.IIO_entry, peb_ldr.IIO_entry)
    kernel32.allocate(mu, peb_ldr.ILO_entry, ntdll.ILO_entry, peb_ldr.IMO_entry, ntdll.IMO_entry, peb_ldr.IIO_entry, ntdll.IIO_entry)

    # initialize stack
    mu.reg_write(UC_X86_REG_ESP, STACK_ADDR)
    mu.reg_write(UC_X86_REG_EBP, STACK_ADDR)

def buildSegmentSelector(val, flg):
    out = flg
    out |= val << 3
    return out

def buildEntry(bottom, maxVal, accessVal, flg):
    out = maxVal & 0xffff
    out |= (bottom & 0xffffff) << 16
    out |= (accessVal & 0xff) << 40
    out |= ((maxVal >> 16) & 0xf) << 48
    out |= (flg & 0xff) << 52
    out |= ((bottom >> 24) & 0xff) << 56
    return pack('<Q',out)

def buildGdt(uc, gdt, loc):
    for idx, val in enumerate(gdt):
        offset = idx * GDT_ENTRY_SIZE
        uc.mem_write(loc + offset, val)

def padDLL(dllPath, dllName):
    global addrTracker
    pe = pefile.PE(dllPath)

    virtualAddress = pe.NT_HEADERS.OPTIONAL_HEADER.DATA_DIRECTORY[0].VirtualAddress
    i = 0
    padding = 0
    while True:
        try:
            section = pe.sections[i]

            pointerToRaw = section.PointerToRawData
            sectionVA = section.VirtualAddress
            sizeOfRawData = section.SizeOfRawData

            if (virtualAddress >= sectionVA and virtualAddress < (sectionVA + sizeOfRawData)):
                padding = virtualAddress - (virtualAddress - sectionVA + pointerToRaw)
                break
        except:
            break

        i += 1


    # Replace e_lfanew value
    elfanew = pe.DOS_HEADER.e_lfanew
    pe.DOS_HEADER.e_lfanew = elfanew + padding

    tmpPath = expandedDLLsPath + dllName
    pe.write(tmpPath)

    # Add padding to dll, then save it.
    out = readRaw(tmpPath)
    final = insertIntoBytes(out, 0x40, padding, 0x00)
    newBin = open(tmpPath, "wb")
    newBin.write(final)
    newBin.close()

    rawDll = readRaw(tmpPath)

    addrTracker = addrTracker + len(rawDll) + 0x1000
    return rawDll


def loadDLLsFromPE(mu):
    path = 'C:\\Windows\\SysWOW64\\'

    for m in mods:
        dll=readRaw(mods[m].d32)

        # Unicorn line to dump the DLL in our memory
        mu.mem_write(mods[m].base, dll)

        pe=pefile.PE(mods[m].d32)
        for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            try:
                export_dict[mods[m].base + exp.address] = (exp.name.decode(), mods[m].name)
            except:
                export_dict[mods[m].base + exp.address] = "unknown_function"
    saveDLLsToFile()        # saving the output to disc by default

def saveDLLsToFile():       #help function called by loaddllsfromPE
    output=""
    for address in export_dict:
        apiName=export_dict[address][0]
        dllName=export_dict[address][1]

        output+=str(hex(address)) +", " + apiName+ ", "  + dllName + "\n"
    with open(foundDLLAddresses, 'w') as out:
        out.write(output)
        out.close()

def loadDLLsFromFile(mu):
    global export_dict
    global expandedDLLsPath
    path = 'C:\\Windows\\SysWOW64\\'

    for m in mods:
        # Inflate dlls so PE offsets are correct

        if os.path.exists("%s%s" % (expandedDLLsPath, mods[m].name)):
            dll=readRaw(expandedDLLsPath+mods[m].name)
            # Unicorn line to dump the DLL in our memory
            mu.mem_write(mods[m].base, dll)
        else:
            dllPath = path + mods[m].name
            rawDll = padDLL(dllPath, mods[m].name)

            # Dump the dll into unicorn memory
            mu.mem_write(mods[m].base, rawDll)

    with open(foundDLLAddresses, "r") as f:
        data = f.read()
    APIs = data.split("\n")
    for each in APIs:
        vals=each.split(", ")
        try:
            address=int(vals[0], 16)
            apiName=vals[1]
            dllName=vals[2]

            if apiName not in export_dict:
                export_dict[address] = ((apiName, dllName))
        except:
            pass

def loadDlls(mu):   # we can keep your function here and then call whichever one it needs. This was easier for me than trying to combine the two in one. :-)
    global loadModsFromFile

    if loadModsFromFile==False:
        loadDLLsFromPE(mu)
    else:
        loadDLLsFromFile(mu)

def push(uc, val):
    # read and subtract 4 from esp
    esp = uc.reg_read(UC_X86_REG_ESP) - 4
    uc.reg_write(UC_X86_REG_ESP, esp)

    # insert new value onto the stack
    uc.mem_write(esp, pack("<i", val))

def constConvert(uc, string):
    if (string == 'eax'):
        return str(uc.reg_read(UC_X86_REG_EAX))
    elif (string == 'ebx'):
        return str(uc.reg_read(UC_X86_REG_EBX))
    elif (string == 'ecx'):
        return str(uc.reg_read(UC_X86_REG_ECX))
    elif (string == 'edx'):
        return str(uc.reg_read(UC_X86_REG_EDX))
    elif (string == 'esi'):
        return str(uc.reg_read(UC_X86_REG_ESI))
    elif (string == 'edi'):
        return str(uc.reg_read(UC_X86_REG_EDI))
    elif (string == 'esp'):
        return str(uc.reg_read(UC_X86_REG_ESP))
    elif (string == 'ebp'):
        return str(uc.reg_read(UC_X86_REG_EBP))

    # Support smaller ebp and esp registers
    elif (string == 'ax'):
        return str(uc.reg_read(UC_X86_REG_AX))
    elif (string == 'bx'):
        return str(uc.reg_read(UC_X86_REG_BX))
    elif (string == 'cx'):
        return str(uc.reg_read(UC_X86_REG_CX))
    elif (string == 'dx'):
        return str(uc.reg_read(UC_X86_REG_DX))
    elif (string == 'si'):
        return str(uc.reg_read(UC_X86_REG_SI))
    elif (string == 'di'):
        return str(uc.reg_read(UC_X86_REG_DI))
    elif (string == 'al'):
        return str(uc.reg_read(UC_X86_REG_AL))
    elif (string == 'bl'):
        return str(uc.reg_read(UC_X86_REG_BL))
    elif (string == 'cl'):
        return str(uc.reg_read(UC_X86_REG_CL))
    elif (string == 'dl'):
        return str(uc.reg_read(UC_X86_REG_DL))
    elif (string == 'sil'):
        return str(uc.reg_read(UC_X86_REG_SIL))
    elif (string == 'dil'):
        return str(uc.reg_read(UC_X86_REG_DIL))

    # Supprt 
    elif (string == 'ah'):
        return str(uc.reg_read(UC_X86_REG_AL))
    elif (string == 'bl'):
        return str(uc.reg_read(UC_X86_REG_BL))
    elif (string == 'cl'):
        return str(uc.reg_read(UC_X86_REG_CL))
    elif (string == 'dl'):
        return str(uc.reg_read(UC_X86_REG_DL))
    elif (string == 'sil'):
        return str(uc.reg_read(UC_X86_REG_SIL))
    elif (string == 'dil'):
        return str(uc.reg_read(UC_X86_REG_DIL))

def callback(match):
    return next(callback.v)

def getJmpFlag(mnemonic, op_str, eflags):
    if re.match("^(je)|(jz)|(jne)|(jnz)", mnemonic, re.M|re.I):
        return "zf"
    elif re.match("^(jg)|(jnle)|(jle)|(jng)", mnemonic, re.M|re.I):
        return "osz"
    elif re.match("^(jge)|(jnl)|(jl)|(jnge)", mnemonic, re.M|re.I):
        return "os"
    else:
        return ""


def controlFlow(uc, mnemonic, op_str):
    controlFlow = re.match("^((jmp)|(ljmp)|(jo)|(jno)|(jsn)|(js)|(je)|(jz)|(jne)|(jnz)|(jb)|(jnae)|(jc)|(jnb)|(jae)|(jnc)|(jbe)|(jna)|(ja)|(jnben)|(jl)|(jnge)|(jge)|(jnl)|(jle)|(jng)|(jg)|(jnle)|(jp)|(jpe)|(jnp)|(jpo)|(jczz)|(jecxz)|(jmp)|(jns)|(call))", mnemonic, re.M|re.I)

    address = 0
    if controlFlow:
        ptr = re.match("d*word ptr \\[.*\\]", op_str)
        if ptr:
            expr = op_str.replace('dword ptr [', '')
            expr = expr.replace(']', '')

            # Support for 64 bit as well.
            # Come up with some more test cases to make sure this works
            regs = re.findall('e[abcdsipx]+', expr)
            for i in range(0, len(regs)):
                regs[i] = constConvert(uc, regs[i])

            callback.v=iter(regs)
            expr = re.sub('e[abcdsipx]+', callback, expr)

            address = eval(expr)
            address = unpack("<I", uc.mem_read(address, 4))[0]
        elif re.match('e[abcdsipx]+', op_str):
            regs = re.findall('e[abcdsipx]+', op_str)
            for i in range(0, len(regs)):
                regs[i] = constConvert(uc, regs[i])

            callback.v=iter(regs)
            address = int(re.sub('e[abcdsipx]+', callback, op_str))

        elif re.match('0x[(0-9)|(a-f)]+', op_str):
            address = int(op_str, 16)
    return address

def ord2(x):
    return x

def show1(int):
        show = "{0:02x}".format(int) #
        return show

def binaryToStr(binary):
    # OP_SPECIAL = b"\x8d\x4c\xff\xe2\x01\xd8\x81\xc6\x34\x12\x00\x00"
    newop=""
    # newAscii=""
    try:
        j = 1
        for v in binary:
            i = ord2(v)
            newop += show1(i)
            if j % 4 == 0:
                newop += " "
            j += 1
        return newop
    except Exception as e:
        print ("*Not valid format")
        print(e)

def binaryToStr2(binary):
    # OP_SPECIAL = b"\x8d\x4c\xff\xe2\x01\xd8\x81\xc6\x34\x12\x00\x00"
    newop=""
    # newAscii=""
    try:
        j = 3
        addr = 0x45b5c290
        a = 0
        while j < len(binary):
            if a % 24 == 0 or a == 0:
                newop += '\n' + hex(addr + a) + ' '
            i = ord2(binary[j])
            newop += show1(i)
            if j % 4 == 0:
                newop += " "
                j += 8
            j -= 1
            a += 1

        newop = newop.replace('0x', '')
        return newop
    except Exception as e:
        print ("*Not valid format")
        print(e)

def setBit (val, pos, newBit):
    if newBit == 0:
        val &= ~(1 << pos)
    else:
        val |= 1 << pos
    return val

def getBit (value, pos):
    return ((value >> pos & 1) != 0)

def flipBit(val, pos):
    return val ^ (1 << pos)

def breakLoop(uc, eflags, jmpFlag):
    if jmpFlag == "zf":
        newEflags = flipBit(eflags, 6)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x46)

# callback for tracing instructions
def hook_code(uc, address, size, user_data):
    global cleanBytes
    global programCounter
    global cleanStackFlag
    global stopProcess
    global prevInstruct
    global prevInstructs
    global loopInstructs
    global loopCounter

    if stopProcess == True:
        uc.emu_stop()

    programCounter += 1
    if programCounter > 500000:
        uc.emu_stop()

    instructLine = ""

    # read this instruction code from memory
    if verbose == True:
        instructLine += "0x%x" % address + '\t'
    shells = uc.mem_read(address, size)
    ret = address
    address = 0

    # Print out the instruction
    mnemonic=""
    op_str=""
    t=0
    for i in cs.disasm(shells, address):
        val = i.mnemonic + " " + i.op_str
        if t==0:
            mnemonic=i.mnemonic
            op_str=i.op_str

        if verbose == True:
            outFile.write(instructLine + val + '\n')
        t+=1

    addr = ret

    # Hook usage of Windows API function
    funcAddress = controlFlow(uc, mnemonic, op_str)
    if funcAddress > KERNEL32_BASE and funcAddress < WSOCK32_TOP:
        ret += size
        push(uc, ret)

        eip = uc.reg_read(UC_X86_REG_EIP)
        esp = uc.reg_read(UC_X86_REG_ESP)
        funcName = export_dict[funcAddress][0]

        try:
            funcInfo, cleanBytes = globals()['hook_'+funcName](uc, eip, esp, export_dict, EXTRA_ADDR)
            logCall(funcName, funcInfo)

            dll = export_dict[funcAddress][1]
            dll = dll[0:-4]

            # Log usage of DLL
            if dll not in logged_dlls:
                logged_dlls.append(dll)

        except:
            # hook_backup(uc, eip, esp, funcAddress, export_dict[funcAddress])
            hook_default(uc, eip, esp, funcAddress, export_dict[funcAddress][0], addr)

        if funcName == 'ExitProcess':
            stopProcess = True
        if 'LoadLibrary' in funcName and uc.reg_read(UC_X86_REG_EAX) == 0:
            stopProcess = True

        uc.reg_write(UC_X86_REG_EIP, EXTRA_ADDR)

    if cleanStackFlag == True:
        cleanStack(uc, cleanBytes)
        cleanStackFlag = False

    # If parameters were used in the function, we need to clean the stack
    if ret == EXTRA_ADDR:
        cleanStackFlag = True

# Most Windows APIs use stdcall, so we need to clean the stack
def cleanStack(uc, numBytes):
    if numBytes > 0:
        esp = uc.reg_read(UC_X86_REG_ESP)
        uc.reg_write(UC_X86_REG_ESP, esp+numBytes)

    # reset cleanBytes
    global cleanBytes
    cleanBytes = 0

def checkDups(uc):
    global prevInstruct
    j = 0
    for i in range (0, len(prevInstruct)-1):
        if prevInstruct[i] == prevInstruct[i+1]:
            j += 1
        else:
            j = 0
        if j == 100:
            uc.emu_stop()
            break

# Get the parameters off the stack
def findDict(funcAddress, funcName):
    # Dict3 #####      'GetProcAddress': (2, ['HMODULE', 'LPCSTR']
    # Dict2 #####      'GetProcAddress': (2, ['HMODULE', 'LPCSTR']
    # Dict1 #####      'GetProcAddress': (2, 8, '.', True)

    global cleanBytes
    dll = export_dict[funcAddress][1]
    dll = dll[0:-4]
    paramVals = []
    dict4 = globals()['dict4_' + dll]
    dict2 = globals()['dict2_' + dll]
    dict1 = globals()['dict_' + dll]

    # Log usage of DLL
    if dll not in logged_dlls:
        logged_dlls.append(dll)

    # Use dict three if we find a record for it
    if funcName in dict3_w32:
        return dict3_w32[funcName], 'dict3'

    # Use dict2 if we can't find the API in dict1
    elif funcName in dict2:
        return dict2[funcName], 'dict2'

    # Use dict four (WINE) if we find a record for it
    elif funcName in dict4:
        return dict4[funcName], 'dict4'

    # If all else fails, use dict 1
    elif funcName in dict1:
        return dict1[funcName], 'dict1'

def getParams(uc, esp, apiDict, dictName):
    global cleanBytes

    paramVals = []


    if dictName == 'dict1':
        numParams = apiDict[0]
        for i in range(0, numParams):
            p = uc.mem_read(esp + (i*4+4), 4)
            p = unpack('<I', p)[0]
            paramVals.append(hex(p))
        cleanBytes = apiDict[1]
    else:
        numParams = apiDict[0]
        for i in range(0, numParams):
            paramVals.append(uc.mem_read(esp + (i*4+4), 4))
            paramVals[i] = unpack('<I', paramVals[i])[0]

            # Check if the type is a string
            if "STR" in apiDict[1][i]:
                try:
                    paramVals[i] = read_string(uc, paramVals[i])
                except:
                    pass
            else:
                paramVals[i] = hex(paramVals[i])

        # Go through all parameters, and see if they can be interpreted as a string
        for i in range (0, len(paramVals)):
            if "STR" not in apiDict[1][i]:
                p = int(paramVals[i], 16)
                if (0x40000000 < p and p < 0x50010000):
                    string = read_string(uc, p)
                    if len(string) < 30:
                        paramVals[i] = string

        cleanBytes = apiDict[0] * 4

    return paramVals

# If we haven't manually implemented the function, we send it to this function
# This function will simply find parameters, then log the call in our dictionary
def hook_default(uc, eip, esp, funcAddress, funcName, callLoc):
    apiDict, dictName = findDict(funcAddress, funcName)
    paramVals = getParams(uc, esp, apiDict, dictName)

    if dictName != 'dict1':
        paramTypes = apiDict[1]
        paramNames = apiDict[2]
    else:
        paramTypes = ['dword'] * len(paramVals)
        paramNames = ['arg'] * len(paramVals)

    retVal = 32
    uc.reg_write(UC_X86_REG_EAX, retVal)

    funcInfo = (funcName, callLoc, hex(retVal), 'INT', paramVals, paramTypes, paramNames)
    logCall(funcName, funcInfo)

def read_string(uc, address):
    ret = ""
    c = uc.mem_read(address, 1)[0]
    read_bytes = 1

    while c != 0x0:
        ret += chr(c)
        c = uc.mem_read(address + read_bytes, 1)[0]
        read_bytes += 1
    return ret

def read_unicode(uc, address):
    ret = ""
    c = uc.mem_read(address, 1)[0]
    read_bytes = 0

    while c != 0x0:
        c = uc.mem_read(address + read_bytes, 1)[0]
        ret += chr(c)
        read_bytes += 2

    return ret

def logCall(funcName, funcInfo):
    global paramValues
    logged_calls[funcName].append(funcInfo)
    loggedList.append(funcInfo)
    paramValues += funcInfo[4]

def logProcessCreate(path):
    createdProcesses.append(path)

def findArtifacts():
    artifacts = []
    net_artifacts = []
    file_artifacts = []
    exec_artifacts = []

    for p in paramValues:
        artifacts += re.findall(r"[a-zA-Z0-9_.-]+\.\S+", p)
        net_artifacts += re.findall(r"http|ftp|https:\/\/?|www\.?[a-zA-Z]+\.com|eg|net|org", p)
        file_artifacts += re.findall(r"[a-zA-z]:\\[^\\]*?\.\S+", p)
        exec_artifacts += re.findall(r"\S+\.exe", p)

    artifacts += net_artifacts + file_artifacts
    return list(dict.fromkeys(artifacts)), list(dict.fromkeys(net_artifacts)), list(dict.fromkeys(file_artifacts)), list(dict.fromkeys(exec_artifacts))

def printCalls():
    print("[*] All API Calls: ")
    print(loggedList)

    print("[*] All DLLs Used: ")
    for dll in logged_dlls:
        print("\t\t", dll)

    artifacts, net_artifacts, file_artifacts, exec_artifacts = findArtifacts()
    print("[*] Artifacts")
    for a in artifacts:
        print("\t\t", a)
    print("[*] Network Artifacts")
    for n in net_artifacts:
        print("\t\t", n)
    print("[*] File Artifacts")
    for f in file_artifacts:
        print("\t\t", f)
    print("[*] Executable Artifacts")
    for e in exec_artifacts:
        print("\t\t", e)

# Test X86 32 bit
def test_i386(mode, code):
    try:
        # Initialize emulator
        mu = Uc(UC_ARCH_X86, mode)

        mu.mem_map(0x40000000, 0x20000000)

        # write machine code to be emulated to memory
        mu.mem_write(CODE_ADDR, code)

        mu.mem_write(EXTRA_ADDR, b'\xC3')

        # initialize stack
        mu.reg_write(UC_X86_REG_ESP, STACK_ADDR)
        mu.reg_write(UC_X86_REG_EBP, STACK_ADDR)

        config_GDT(mu, code)

        global cs
        if mode == UC_MODE_32:
            print("[*] Emulating x86_32 code")
            cs = Cs(CS_ARCH_X86, CS_MODE_32)
            allocateWinStructs32(mu)

        elif mode == UC_MODE_64:
            print("[*] Emulating x86_64 code")
            cs = Cs(CS_ARCH_X86, CS_MODE_64)
            allocateWinStructs64(mu)

        loadDlls(mu)

        # tracing all instructions with customized callback
        mu.hook_add(UC_HOOK_CODE, hook_code)

        # emulate machine code in infinite time
        try:
            mu.emu_start(CODE_ADDR, CODE_ADDR + len(code))
        except:
            pass

        # now print out some registers
        print("[*] Emulation done")
        printCalls()

    except UcError as e:
        print("ERROR: %s" % e)

if __name__ == '__main__':
    global verbose
    code = b""
    verbose = True
    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--mode')
    parser.add_argument('-f', '--file')
    # parser.add_argument('-v', '--verbose', action='count', default=0)

    args = parser.parse_args()

    # if args.verbose == 1:
    #     verbose = True

    if args.file != None:
        code = readRaw(args.file)

    if args.mode == '32':
        test_i386(UC_MODE_32, code)