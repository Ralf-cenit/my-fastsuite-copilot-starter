"""Arnold2D downloader with ISO program output.

Rules and conventions: Arnold/Arnold2D.md.
"""

import sys, inspect, os, math
sys.dont_write_bytecode = True
sys.path.append(str(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))))

from cenpylib import FileUtility
from cenpydownload import *
from cenpyolpcore import *


DOWNLOAD_CLASS_NAME = "Arnold2D"
SOURCEFILE_EXTENSION = ".iso"
DATAFILE_EXTENSION = ".iso"

PRECISION_COORD = 3
PRECISION_ANGLE = 3
PRECISION_SPEED = 3
COORDINATE_SEPARATOR = "="
UNIT_FACTOR = 1000.0


def fmt(value: float, precision: int) -> str:
    """Trim trailing zeros, retaining one fractional digit and normalizing zero."""
    if value == 0.0:
        value = 0.0
    formatted = f"{value:.{precision}f}".rstrip('0')
    if formatted.endswith('.'):
        formatted += '0'
    if formatted == "-0.0":
        formatted = "0.0"
    return formatted


def _coord(value): return fmt(value, PRECISION_COORD)
def _angle(value): return fmt(value, PRECISION_ANGLE)
def _speed(value): return fmt(value, PRECISION_SPEED)


class Arnold2D(Downloader):
   """Arnold2D downloader with G-code motion handling."""

   def __init__(self) -> None:
      super().__init__()
      self.FileUtil = FileUtility()
      self.Logging = None
      self.OnDev = False
      self.OutputFilePath = ""
      self.ProgramName = ""
      self.Header = []
      self.Source = []
      self.Footer = []
      self.CurrentLinFeedrate = 0
      self.CurrentPtpFeedrate = 0
      self.CIPCircle = False
      self._blockNumber = 0
      self._currentBlockNumber = 0
      self._rotationAxisMap = {
         "A": "B",    # world-X rotation A outputs on controller A (None if not used)
         "B": None,   # world-Y rotation B outputs on controller B (None if not used)
         "C": "C",    # world-Z rotation C outputs on controller C (None if not used)
      }
      self._suppressMotion = False
      self._lastEndXYZ = [0.0, 0.0, 0.0]
      self._modal = {
         "X": None, "Y": None, "Z": None,
         "I": None, "J": None, "K": None,
         "A": None, "B": None, "C": None,
         "F": None,
      }
      self._activeBaseZ = [0.0, 0.0, 1.0]
      self.PulseValues = []
      self.GroupMap = []
      self.SuppressConfig = False
      self.SpeedUnit = "mm/s"
      self._currentOperationGroupName = ""

   def DevLogging(self, info):
      if self.OnDev and self.Logging is not None:
         self.Logging.LogInfo(info)

   def _computeBaseZ(self, baseProfile):
      """Return world-space base Z, falling back to (0, 0, 1)."""
      if baseProfile is None:
         return [0.0, 0.0, 1.0]
      rx, ry, rz = baseProfile.GetOrientation()
      cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
      cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
      cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
      return [cz*sy*cx + sz*sx,
              sz*sy*cx - cz*sx,
              cy*cx]

   def _refreshBaseZ(self, olpObject):
      if olpObject is None:
         return
      base = olpObject.GetUsedBaseProfile()
      if base is not None:
         self._activeBaseZ = self._computeBaseZ(base)

   def _circularGCode(self, start, via, end):
      first = [via[index] - start[index] for index in range(3)]
      second = [end[index] - via[index] for index in range(3)]
      normal_x = first[1]*second[2] - first[2]*second[1]
      normal_y = first[2]*second[0] - first[0]*second[2]
      normal_z = first[0]*second[1] - first[1]*second[0]
      dot = (normal_x*self._activeBaseZ[0]
             + normal_y*self._activeBaseZ[1]
             + normal_z*self._activeBaseZ[2])
      return "G02" if dot < 0 else "G03"

   def _circularCenterOffset(self, start, via, end):
      """Return the 3D circumcenter minus start, with all inputs in millimeters."""
      def cross(first, second):
         return [
            first[1]*second[2] - first[2]*second[1],
            first[2]*second[0] - first[0]*second[2],
            first[0]*second[1] - first[1]*second[0],
         ]
      
      to_via = [via[index] - start[index] for index in range(3)]
      to_end = [end[index] - start[index] for index in range(3)]
      normal = cross(to_via, to_end)
      via_squared = sum(value*value for value in to_via)
      end_squared = sum(value*value for value in to_end)
      normal_squared = sum(value*value for value in normal)
      if normal_squared <= 1e-24 * via_squared * end_squared:
         raise ValueError("Cannot calculate arc center from coincident or collinear points.")
      end_cross_normal = cross(to_end, normal)
      normal_cross_via = cross(normal, to_via)
      return [
         (via_squared*end_cross_normal[index] + end_squared*normal_cross_via[index])
         / (2.0*normal_squared)
         for index in range(3)
      ]

   def _writeLine(self, line: str):
      if self._suppressMotion:
         return
      if self._blockNumber > 0 and line.strip():
         self._currentBlockNumber += self._blockNumber
         line = f"N{self._currentBlockNumber} {line}"
      self.FileUtil.AppendTextToFile(self.OutputFilePath, line)

   def _resetModalState(self):
      for address in self._modal:
         self._modal[address] = None

   def Initialize(self, operator: DULPythonDownloadOperator):
      self.Logging = operator.GetLogOperator()
      self.DevLogging("================= Arnold2D Download started =================")
      controller = operator.GetController()
      program = controller.GetActiveProgram()
      self.ProgramName = program.GetName()
      self._currentOperationGroupName = ""
      circle_output = program.GetLiteral("CIRCLE_OUTPUT", False)
      self.CIPCircle = circle_output == "CIP"
      self.OutputFilePath = os.path.join(controller.GetOutputDirectory(),
                                        self.ProgramName + SOURCEFILE_EXTENSION)
      self.CurrentLinFeedrate = 0.0
      self._currentBlockNumber = 0
      self._lastEndXYZ = [0.0, 0.0, 0.0]
      self._resetModalState()
      self._activeBaseZ = self._computeBaseZ(program.GetUsedBaseProfile())
      for resource in controller.GetResources():
         if resource.GetItemType().name != 'Production':
            continue
         if resource.GetItemSubType().name != 'MachineRobot':
            continue
         for attribute in resource.GetAttributes():
            name = attribute.GetName()
            if name == 'CENOlpHonkyTonkPulses':
               self.PulseValues = str(attribute.GetValue()).split(';')
            elif name == 'CENOlpHonkyTonkGroupMap':
               self.GroupMap = str(attribute.GetValue()).split(';')
            elif name == 'CENOlpSuppressConfig':
               self.SuppressConfig = resource.GetBool('CENOlpSuppressConfig', 1)
            elif name == 'CENOlpHonkyTonkSpeedUnit':
               self.SpeedUnit = str(attribute.GetValue())

   def ProgramStart(self, operator, program: DULPythonProgram):
      self._refreshBaseZ(program)
      self.Header = [
         f"%_N_{self.ProgramName}_MPF",
         f";$PATH=/_N_WKS_DIR/_N_{self.ProgramName}_WPD",
         "MAT_",
         "back:",
         "READY_TABLE",
         "G90 G64 SOFT",
         "T1",
         "G0 Z=0",
         "G54",
         ";--------------",
         "TRANS X=0.0 Y=0.0",
         f"{self.ProgramName}_SUB",
         ";--------------",
         "CHANGE_TABLE     ;start the changing of the tables",
         "GOTOB back",
         "M30",
         "",
         f"%_N_{self.ProgramName}_SUB_SPF",
         f";$PATH=/_N_WKS_DIR/_N_{self.ProgramName}_WPD",
         "G90",
      ]
      for line in self.Header:
         self._writeLine(line)

   def ProgramEnd(self, operator, program: DULPythonProgram):
      self.Footer = [
         f";END OF GENERATION OF : {self.ProgramName}",
         "G90",
         "PP7",
         "M17",
      ]
      for line in self.Footer:
         self._writeLine(line)

   def OperationGroupStart(self, operator, group: DULPythonOperationGroup):
      self._currentOperationGroupName = group.GetName()
      self._refreshBaseZ(group)

   def OperationGroupEnd(self, operator, group: DULPythonOperationGroup):
      self._currentOperationGroupName = ""

   def OperationStart(self, operator, operation: DULPythonOperation):
      self._refreshBaseZ(operation)
      self._resetModalState()
      self._writeLine(f";START OF {self._currentOperationGroupName} - {operation.GetName()}")

   def OperationEnd(self, operator, operation: DULPythonOperation):
      self._suppressMotion = False
      self._resetModalState()
      self._writeLine(f";END OF {self._currentOperationGroupName} - {operation.GetName()}")

   def HandleEvent(self, operator: DULPythonDownloadOperator,
                   currentMotion: DULPythonMotion,
                   event: DULPythonEvent):
      if event.GetName() == 'LaserOn':
         self._writeLine('PP3')
      elif event.GetName() == 'LaserOff':
         self._writeLine('PP6')
      elif event.GetName() == 'RegshapeRetract' or event.GetName() == 'RegshapeOnePointRetract' or event.GetName() == 'RegshapeTwoPointRetract':
         self._suppressMotion = False
         self._resetModalState()
      elif event.GetName() == 'CIRCLE':
         self.CircleRegshape(operator, currentMotion, event)
      # elif event.GetName() == 'SLOT':
      #    self.SlotRegshape(operator, currentMotion, event)
      # elif event.GetName() == 'RECTANGLE':
      #    self.RectangleRegshape(operator, currentMotion, event)
      else:
         self.HandleBuildInEvents(operator, event)
      for motion in event.GetMotions():
         self.HandleMotion(operator, motion)

   def HandleBuildInEvents(self, operator: DULPythonDownloadOperator,
                            event: DULPythonEvent):
      name = event.GetName()
      if name == 'Speed': self.SetSpeed(operator, event)
      elif name == 'Accuracy': self.SetAccuracy(operator, event)
      elif name == 'Acceleration': self.SetAcceleration(operator, event)
      elif name == 'Dwell': self.OutputDwellEvent(operator, event)
      elif name == 'LogicPort': self.LogicPortEvent(operator, event)
      elif name == 'TextEvent': self.TextEvent(operator, event)

   def SetSpeed(self, operator: DULPythonDownloadOperator, event: DULPythonEvent):
      speed = 0.0
      pathtype = ''
      for attribute in event.GetAttributes():
         if attribute.GetName() == 'Value': speed = attribute.GetValue()
         elif attribute.GetName() == 'PathType': pathtype = attribute.GetValue()
      if pathtype == 'Contour':
         self.CurrentLinFeedrate = int(speed * UNIT_FACTOR)
      else:
         self.CurrentPtpFeedrate = int(speed)

   def SetAccuracy(self, operator, event): pass
   def SetAcceleration(self, operator, event): pass
   def OutputDwellEvent(self, operator, event):
      dwell_time = 0.0
      for attribute in event.GetAttributes():
         if attribute.GetName() == "Value":
            dwell_time = float(attribute.GetValue())
            break
      if dwell_time > 0.0:
         self._writeLine(f"G04 F{fmt(dwell_time, 1)}")
   def LogicPortEvent(self, operator, event): pass
   def TextEvent(self, operator, event):
      text = ""
      is_comment = False
      for attribute in event.GetAttributes():
         if attribute.GetName() == "IsComment":
            is_comment = attribute.GetValue()
         elif attribute.GetName() == "Text":
            text = str(attribute.GetValue())
      if is_comment:
         self._writeLine(";" + text)

   def _regshapeRotationLines(self, currentMotion):
      orientation = currentMotion.GetPosition().GetOrientation()
      rotation_tokens = []
      rotation_addresses = []
      for index, world_axis in enumerate("ABC"):
         mapped_axis = self._rotationAxisMap.get(world_axis)
         if mapped_axis is not None:
            output_address = "XYZ"["ABC".index(mapped_axis)]
            rotation_addresses.append(output_address)
            rotation_tokens.append(
               f"{output_address}{fmt(orientation[index], PRECISION_ANGLE)}")
      rotation_line = "ROT " + " ".join(rotation_tokens) if rotation_tokens else None
      reset_line = ("ROT " + " ".join(f"{address}0.0" for address in rotation_addresses)
                    if rotation_addresses else None)
      return rotation_line, reset_line

   def CircleRegshape(self, operator, currentMotion, event):
      radius = 0.0
      xyz = currentMotion.GetPosition().GetXYZ()
      explode_regshape = False
      for attribute in event.GetAttributes():
         if attribute.GetName() == "Radius":
            radius = float(attribute.GetValue())
         elif attribute.GetName() == "ExplodeRegshape":
            explode_regshape = attribute.GetBool()
      lines = [
         f"; START OF :{event.GetName()}",
         f"HOLEPOS_X  ={_coord(xyz[0] * UNIT_FACTOR)}",
         f"HOLEPOS_Y  ={_coord(xyz[1] * UNIT_FACTOR)}",
         f"HOLE_RADIUS={_coord(radius * UNIT_FACTOR)}",
         "CUT_HOLE",
         f"; END OF :{event.GetName()}",
      ]
      for line in lines:
         self._writeLine(line)
      if not explode_regshape:
         self._suppressMotion = True

   def SlotRegshape(self, operator, currentMotion, event):
      length = 0.0
      width = 0.0
      radius = 0.0
      rotation = 0.0
      regshpfeed = 0.0
      rotation_line, reset_line = self._regshapeRotationLines(currentMotion)
      explode_regshape = False
      for attribute in event.GetAttributes():
         if attribute.GetName() == "Length":
            length = float(attribute.GetValue())
         elif attribute.GetName() == "Width":
            width = float(attribute.GetValue())
         elif attribute.GetName() == "Radius":
            radius = float(attribute.GetValue())
         elif attribute.GetName() == "LC_LD_REGSHAPE_ROTATION":
            rotation = float(attribute.GetValue())
         elif attribute.GetName() == "RegshapeCuttingFeedrate":
            regshpfeed = float(attribute.GetValue())
         elif attribute.GetName() == "ExplodeRegshape":
            explode_regshape = attribute.GetBool()
      lines = [
         f"; START OF :{event.GetName()}",
      ]
      if rotation_line:
         lines.append(rotation_line)
      lines.extend((
         "G91",
         f"F{fmt(regshpfeed, PRECISION_SPEED)}",
         f"R1={fmt(length * UNIT_FACTOR, PRECISION_COORD)}",
         f"R2={fmt(width * UNIT_FACTOR, PRECISION_COORD)}",
         f"R3={fmt(radius * UNIT_FACTOR, PRECISION_COORD)}",
         f"AROT Z{fmt(rotation, PRECISION_ANGLE)}",
         "ZYCL_RQ",
      ))
      if reset_line:
         lines.append(reset_line)
      lines.extend(("G90", f"; END OF :{event.GetName()}"))
      for line in lines:
         self._writeLine(line)
      if not explode_regshape:
         self._suppressMotion = True

   def RectangleRegshape(self, operator, currentMotion, event):
      length = 0.0
      width = 0.0
      radius = 0.0
      rotation = 0.0
      regshpfeed = 0.0
      rotation_line, reset_line = self._regshapeRotationLines(currentMotion)
      explode_regshape = False
      for attribute in event.GetAttributes():
         if attribute.GetName() == "Length":
            length = float(attribute.GetValue())
         elif attribute.GetName() == "Width":
            width = float(attribute.GetValue())
         elif attribute.GetName() == "Radius":
            radius = float(attribute.GetValue())
         elif attribute.GetName() == "LC_LD_REGSHAPE_ROTATION":
            rotation = float(attribute.GetValue())
         elif attribute.GetName() == "RegshapeCuttingFeedrate":
            regshpfeed = float(attribute.GetValue())
         elif attribute.GetName() == "ExplodeRegshape":
            explode_regshape = attribute.GetBool()
      lines = [
         f"; START OF :{event.GetName()}",
      ]
      if rotation_line:
         lines.append(rotation_line)
      lines.extend((
         "G91",
         f"F{fmt(regshpfeed, PRECISION_SPEED)}",
         f"R1={fmt(length * UNIT_FACTOR, PRECISION_COORD)}",
         f"R2={fmt(width * UNIT_FACTOR, PRECISION_COORD)}",
         f"R3={fmt(radius * UNIT_FACTOR, PRECISION_COORD)}",
         f"AROT Z{fmt(rotation, PRECISION_ANGLE)}",
         "ZYCL_RQ",
      ))
      if reset_line:
         lines.append(reset_line)
      lines.extend(("G90", f"; END OF :{event.GetName()}"))
      for line in lines:
         self._writeLine(line)
      if not explode_regshape:
         self._suppressMotion = True

   def _modalToken(self, addr: str, formatted: str, commit: dict, force: bool = False) -> str:
      commit[addr] = formatted
      if not force and self._modal.get(addr) == formatted:
         return ""
      separator = ""
      if not self.CIPCircle and addr in ("X", "Y", "Z", "I", "J", "K", "A", "B", "C"):
         separator = COORDINATE_SEPARATOR
      return f"{addr}{separator}{formatted}"

   def HandleMotion(self, operator, motion: DULPythonMotion):
      for event in motion.GetEventsBefore():
         self.HandleEvent(operator, motion, event)
      if self._suppressMotion:
         for event in motion.GetEventsAfter():
            self.HandleEvent(operator, motion, event)
         return
      position = motion.GetPosition()
      xyz_m = position.GetXYZ()
      rpy = position.GetOrientation()
      mtype = motion.GetMotionType()
      coord_x, coord_y, coord_z = (value * UNIT_FACTOR for value in xyz_m)
      speed = self.CurrentLinFeedrate
      isCircular = motion.IsCircularMotion()
      useCIP = isCircular and self.CIPCircle
      if isCircular:
         via_m = motion.GetViaPosition().GetXYZ()
         via_x, via_y, via_z = (value * UNIT_FACTOR for value in via_m)
         prefix = "CIP" if useCIP else self._circularGCode(
            self._lastEndXYZ, [via_x, via_y, via_z], [coord_x, coord_y, coord_z])
         emitF = True
      elif motion.IsLinearMotion():
         isJoint = hasattr(mtype, "name") and mtype.name == "Joint"
         prefix = "G00" if isJoint else "G01"
         emitF = not isJoint
      else:
         prefix = "G00"
         emitF = False
      commit = {}
      tokens = [
         self._modalToken("X", _coord(coord_x), commit, useCIP),
         self._modalToken("Y", _coord(coord_y), commit, useCIP),
         self._modalToken("Z", _coord(coord_z), commit, useCIP),
      ]
      if useCIP:
         tokens = [
            f"I1={_coord(via_x)}",
            f"J1={_coord(via_y)}",
            f"K1={_coord(via_z)}",
         ] + tokens
      elif isCircular:
         offset_x, offset_y, offset_z = self._circularCenterOffset(
            self._lastEndXYZ, [via_x, via_y, via_z], [coord_x, coord_y, coord_z])
         tokens += [
            self._modalToken("I", _coord(offset_x), commit),
            self._modalToken("J", _coord(offset_y), commit),
         ]
      if useCIP:
         for index, world_axis in enumerate("ABC"):
            output_address = self._rotationAxisMap[world_axis]
            if output_address is not None:
               tokens.append(
                  self._modalToken(output_address, _angle(rpy[index]), commit, True))
      if emitF:
         tokens.append(self._modalToken("F", _speed(speed), commit))
      output_tokens = [token for token in tokens if token]
      isReference = motion.IsReferenceMotion()
      if output_tokens:
         block = " ".join([prefix] + output_tokens)
         if isReference:
            block = ";" + block
         self._writeLine(block)
      if not isReference:
         self._lastEndXYZ = [coord_x, coord_y, coord_z]
         self._modal.update(commit)
      for event in motion.GetEventsAfter():
         self.HandleEvent(operator, motion, event)

   def CreateOutputFile(self, operator):
      pass

   def WriteOutputFile(self, operator):
      pass

   def CloseOutputFile(self, operator):
      operator.AddOutputFilePath(self.OutputFilePath)
      self.DevLogging("================= Arnold2D Download finished =================")