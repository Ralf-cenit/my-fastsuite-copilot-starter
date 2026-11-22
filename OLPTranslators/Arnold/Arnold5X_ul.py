"""Import Arnold5X ISO motions and technology events into FASTSUITE."""

import math
import ntpath
import re
import sys

from cenpyupload import Uploader
from cenpydownload import MotionType, ProcessType, TargetType
from cenpyolpcore import InsertPosition

sys.dont_write_bytecode = True

UPLOAD_CLASS_NAME = "Arnold5XUploader"
UNIT_FACTOR = 1000.0

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
_WORD = re.compile(r"(I1|J1|K1|[XYZABCF])\s*=?\s*(" + _NUMBER + r")", re.I)
_BLOCK = re.compile(r"^N\d+\s*", re.I)
_START = re.compile(r"^;\s*START OF\s+(.+?)\s*$", re.I)
_END = re.compile(r"^;\s*END OF(?:\s|$)", re.I)


class Arnold5XUploader(Uploader):
    def __init__(self):
        super().__init__()
        self._reset()
        self._logging = None

    def _reset(self):
        self._program = None
        self._group = None
        self._operation = None
        self._last_motion = None
        self._groups = {}
        self._axes = dict.fromkeys("XYZABC", 0.0)
        self._motion_type = MotionType.Linear
        self._events = []
        self._text = []
        self._shape_parameters = {}
        self._shape_rotation = 0.0
        self._shape_feed = 0.0
        self._motion_count = 0
        self._operation_count = 0

    def Initialize(self, operator):
        self._reset()

    def Finalize(self, operator):
        pass

    def ParseFile(self, operator, fileObject):
        self._reset()
        self._logging = operator.GetLogOperator()
        name = ntpath.splitext(ntpath.basename(fileObject.name))[0]
        self._program = operator.CreateEmptyProgram()
        self._program.SetName(name)
        self._program.SetIsMainProgram(True)
        for line_number, raw in enumerate(fileObject, 1):
            text = raw.rstrip("\r\n")
            line = _BLOCK.sub("", text.strip().lstrip("\ufeff"))
            self._logging.LogInfo("upload : " + str(line))
            if not line:
                continue
            try:
                self._parse_line(operator, line, text)
            except ValueError as error:
                message = f"Arnold5X: line {line_number}: {error} ({text})"
                operator.GetLogOperator().LogError(message)
                raise ValueError(message) from error
        self._flush_text(operator)
        if self._last_motion is not None:
            for event in self._events:
                event.SetInsertPosition(InsertPosition.InsertAfter)
                self._last_motion.AddEventAfter(event)
        elif self._events:
            operator.GetLogOperator().LogWarning(
                "Arnold5X: no motion found; pending events cannot be attached.")
        self._events = []

    def _open_operation(self, operator, marker=None):
        if marker is not None:
            names = re.split(r"\s+-\s+", marker, maxsplit=1)
            group_name = names[0].strip()
            operation_name = names[1].strip() if len(names) == 2 else group_name
        else:
            group_name = self._group.GetName() if self._group is not None else "GRP001"
            operation_name = f"OP{self._operation_count + 1:03d}"
        if group_name not in self._groups:
            group = operator.CreateEmptyOperationGroup()
            group.SetName(group_name)
            self._program.AddOperationGroup(group)
            self._groups[group_name] = group
        self._group = self._groups[group_name]
        self._operation = operator.CreateEmptyOperation()
        self._operation.SetName(operation_name)
        self._group.AddOperation(self._operation)
        self._operation_count += 1

    def _event(self, operator, name, attributes):
        event = operator.CreateEmptyEvent()
        event.SetName(name)
        event.SetInsertPosition(InsertPosition.InsertBefore)
        setter = operator.GetAttributeSetterOperator()
        creators = {
            bool: setter.CreateWritingBoolAttributesObject,
            float: setter.CreateWritingDoubleAttributesObject,
            str: setter.CreateWritingStringAttributesObject,
        }
        for attribute_name, value in attributes.items():
            attribute = creators[type(value)]()
            attribute.SetName(attribute_name)
            attribute.SetValue(value)
            event.AddAttribute(attribute)
        return event

    def _flush_text(self, operator):
        if self._text:
            separator = "\n"
            self._events.append(self._event(operator, "TextEvent", {
                "Text": separator.join(self._text),
                "IsMultiLine": len(self._text) > 1,
                "MultiLineSeparator": separator,
                "IsComment": False,
            }))
            self._text = []

    def _position(self, operator, axes, name, process_type):
        position = operator.CreateEmptyPosition()
        position.SetName(name)
        position.SetTargetType(TargetType.Cartesian)
        position.SetProcessType(process_type)
        position.SetXYZ(tuple(axes[axis] / UNIT_FACTOR for axis in "XYZ"))
        position.SetOrientation(tuple(axes[axis] for axis in "ABC"))
        return position

    def _add_motion(self, operator, motion_type, via=None, process_type=None):
        if self._operation is None:
            self._open_operation(operator)
        self._flush_text(operator)
        self._motion_count += 1
        name = f"P{self._motion_count:04d}"
        motion = operator.CreateEmptyMotion()
        motion.SetName(name)
        motion.SetMotionType(motion_type)
        if process_type is None:
            process_type = (ProcessType.Auxiliary if motion_type == MotionType.Pt2Pt
                            else ProcessType.ProcessCurve)
        motion.SetPosition(self._position(operator, self._axes, name, process_type))
        if via is not None:
            motion.SetViaPosition(self._position(operator, via, name + "_VIA", ProcessType.ViaPoint))
        motion.SetEventsBefore(self._events)
        self._events = []
        self._operation.AddMotion(motion)
        self._last_motion = motion

    def _regshape_direction_x(self, rotation):
        angle_a, angle_b, angle_c = map(math.radians, (self._axes["A"], self._axes["B"], self._axes["C"]))
        angle_rotation = math.radians(rotation)
        local_x = math.cos(angle_rotation)
        local_y = math.sin(angle_rotation)
        rotated_x = math.cos(angle_b) * local_x + math.sin(angle_b) * math.sin(angle_a) * local_y
        rotated_y = math.cos(angle_a) * local_y
        rotated_z = -math.sin(angle_b) * local_x + math.cos(angle_b) * math.sin(angle_a) * local_y
        self._logging.LogInfo("............................_regshape_direction_x X: " + str(math.cos(angle_c) * rotated_x - math.sin(angle_c) * rotated_y))
        self._logging.LogInfo("............................_regshape_direction_x Y: " + str(math.sin(angle_c) * rotated_x + math.cos(angle_c) * rotated_y))
        self._logging.LogInfo("............................_regshape_direction_x Z: " + str(rotated_z))
        return (
            math.cos(angle_c) * rotated_x - math.sin(angle_c) * rotated_y,
            math.sin(angle_c) * rotated_x + math.cos(angle_c) * rotated_y,
            rotated_z,
        )

    def _regshape_direction_z(self):
        angle_a, angle_b, angle_c = map(math.radians, (self._axes["A"], self._axes["B"], self._axes["C"]))
        direction_x = math.cos(angle_c) * math.sin(angle_b) * math.cos(angle_a) + math.sin(angle_c) * math.sin(angle_a)
        direction_y = math.sin(angle_c) * math.sin(angle_b) * math.cos(angle_a) - math.cos(angle_c) * math.sin(angle_a)
        direction_z = math.cos(angle_b) * math.cos(angle_a)
        self._logging.LogInfo("............................_regshape_direction_z X: " + str(direction_x))
        self._logging.LogInfo("............................_regshape_direction_z Y: " + str(direction_y))
        self._logging.LogInfo("............................_regshape_direction_z Z: " + str(direction_z))
        return direction_x, direction_y, direction_z

    def _regshape(self, operator, cycle):
        if self._last_motion is None or self._operation is None:
            raise ValueError("Regshape requires a preceding motion in the operation")
        parameters = self._shape_parameters
        required = ("R4",) if cycle == "ZYCL_KR" else ("R1", "R2", "R3")
        if any(name not in parameters for name in required):
            raise ValueError("Missing Regshape parameters: " + ", ".join(required))
        if any(parameters[name] <= 0 for name in required if name != "R3"):
            raise ValueError("Regshape dimensions must be positive")
        self._logging.LogInfo("............................self._shape_rotation: " + str(self._shape_rotation))
        rotation = self._shape_rotation + (90.0 if self._axes["B"] >= 0 else -90.0)
        self._logging.LogInfo("............................self._shape_rotation: " + str(rotation))
        direction_x = self._regshape_direction_x(rotation)
        direction_z = self._regshape_direction_z()
        attributes = {
            "LC_LD_REGSHAPE_ROTATION": rotation,
            "LC_LD_REGSHAPE_DIRX_X": direction_x[0],
            "LC_LD_REGSHAPE_DIRX_Y": direction_x[1],
            "LC_LD_REGSHAPE_DIRX_Z": direction_x[2],
            "LC_LD_REGSHAPE_DIRZ_X": direction_z[0],
            "LC_LD_REGSHAPE_DIRZ_Y": direction_z[1],
            "LC_LD_REGSHAPE_DIRZ_Z": direction_z[2],
            "LC_LD_REGSHAPE_TEST_XXX": 1.23456,
            "RegshapeCuttingFeedrate": self._shape_feed / UNIT_FACTOR,
            "ExplodeRegshape": False,
        }
        if cycle == "ZYCL_KR":
            name = "CIRCLE"
            attributes["Radius"] = parameters["R4"] / (2 * UNIT_FACTOR)
        else:
            width, radius = parameters["R2"], parameters["R3"]
            if radius < 0 or 2 * radius > width + 1e-6:
                raise ValueError("Regshape corner radius exceeds half the width")
            name = "SLOT" if math.isclose(2 * radius, width, abs_tol=1e-6) else "RECTANGLE"
            attributes["Length"] = parameters["R1"] / UNIT_FACTOR
            attributes["Radius"] = radius / UNIT_FACTOR
            if name == "RECTANGLE":
                attributes["Width"] = width / UNIT_FACTOR
        self._flush_text(operator)
        self._events.append(self._event(operator, name, attributes))
        self._add_motion(operator, MotionType.Linear, process_type=ProcessType.ProcessPoint)
        self._shape_parameters = {}
        self._shape_rotation = 0.0
        self._shape_feed = 0.0

    def _parse_line(self, operator, line, original):
        start = _START.fullmatch(line)
        if start:
            self._flush_text(operator)
            self._open_operation(operator, start.group(1))
            return
        if _END.match(line):
            self._flush_text(operator)
            self._operation = None
            return
        code, separator, comment = line.partition(";")
        code = code.strip().upper()
        if code in ("PP3", "PP6"):
            self._flush_text(operator)
            self._events.append(self._event(
                operator, "LaserOn" if code == "PP3" else "LaserOff", {}))
        elif code in ("ZYCL_KR", "ZYCL_RQ"):
            self._regshape(operator, code)
        elif re.fullmatch(r"(?:A?ROT)\s+.*", code):
            rotation = re.fullmatch(r"AROT\s+Z\s*(" + _NUMBER + r")", code)
            if rotation:
                self._shape_rotation = float(rotation.group(1))
            elif code.startswith("AROT") or _WORD.sub("", code[3:]).strip():
                self._text.append(original)
                return
        elif code in ("G90", "G91"):
            pass
        elif re.fullmatch(r"R[1-4]\s*=\s*" + _NUMBER, code):
            address, value = code.split("=", 1)
            self._shape_parameters[address.strip()] = float(value)
        elif re.fullmatch(r"F\s*=?\s*" + _NUMBER, code):
            self._shape_feed = float(code.lstrip("F").lstrip(" ="))
        elif self._parse_motion(operator, code):
            pass
        else:
            self._text.append(original)
            return
        if separator and comment.strip():
            self._text.append(";" + comment)

    def _parse_motion(self, operator, code):
        command = re.match(r"^(G0?[01]|CIP)(?=\s|[XYZABCF]|$)", code)
        body = code[command.end():] if command else code
        words = list(_WORD.finditer(body))
        if not words and command is None:
            return False
        if _WORD.sub("", body).strip():
            return False
        values = {match.group(1).upper(): float(match.group(2)) for match in words}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("Non-finite motion value")
        motion_type = self._motion_type
        if command:
            motion_type = {"G0": MotionType.Pt2Pt, "G00": MotionType.Pt2Pt,
                           "G1": MotionType.Linear, "G01": MotionType.Linear,
                           "CIP": MotionType.Circular}[command.group(1)]
        has_axes = any(axis in values for axis in self._axes)
        via = None
        if motion_type == MotionType.Circular and has_axes:
            if not all(axis in values for axis in ("I1", "J1", "K1")):
                raise ValueError("CIP requires I1, J1 and K1")
            via = dict(self._axes)
            via.update(zip("XYZ", (values[axis] for axis in ("I1", "J1", "K1"))))
        elif any(axis in values for axis in ("I1", "J1", "K1")):
            raise ValueError("Via coordinates require a CIP motion")
        self._motion_type = motion_type
        self._axes.update((axis, values[axis]) for axis in self._axes if axis in values)
        if "F" in values:
            self._flush_text(operator)
            self._events.append(self._event(operator, "Speed", {
                "Value": values["F"] / UNIT_FACTOR,
                "PathType": "Joint" if motion_type == MotionType.Pt2Pt else "Contour",
            }))
        if has_axes:
            self._add_motion(operator, motion_type, via)
        return True