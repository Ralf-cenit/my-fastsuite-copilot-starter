"""Parser regression tests without the native FASTSUITE host."""

import importlib.util
import io
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


ARNOLD_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("arnold5x_under_test", ARNOLD_DIR / "Arnold5X_ul.py")
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {
    "cenpyupload": types.SimpleNamespace(Uploader=object),
    "cenpydownload": types.SimpleNamespace(
        MotionType=types.SimpleNamespace(Linear=1, Pt2Pt=2, Circular=3),
        ProcessType=types.SimpleNamespace(Auxiliary=1, ProcessCurve=2, ViaPoint=3, ProcessPoint=4),
        TargetType=types.SimpleNamespace(Cartesian=1)),
    "cenpyolpcore": types.SimpleNamespace(
        InsertPosition=types.SimpleNamespace(InsertBefore=1, InsertAfter=2)),
}):
    SPEC.loader.exec_module(MODULE)


class Arnold5XUploadTests(unittest.TestCase):
    def setUp(self):
        self.created = {}
        factories = ["CreateEmpty" + kind for kind in (
            "Program", "OperationGroup", "Operation", "Motion", "Position", "Event")]
        self.operator = Mock(spec_set=factories + ["GetAttributeSetterOperator", "GetLogOperator"])
        methods = [
            "SetName", "GetName", "SetIsMainProgram", "AddOperationGroup", "AddOperation",
            "AddMotion", "SetMotionType", "SetPosition", "SetViaPosition", "SetEventsBefore",
            "AddEventAfter", "SetTargetType", "SetProcessType", "SetXYZ", "SetOrientation",
            "SetInsertPosition", "AddAttribute", "SetValue",
        ]

        def create(kind):
            result = Mock(spec_set=methods)
            result.GetName.side_effect = lambda: result.SetName.call_args.args[0]
            self.created.setdefault(kind, []).append(result)
            return result

        for factory in factories:
            getattr(self.operator, factory).side_effect = lambda kind=factory: create(kind)
        setter = self.operator.GetAttributeSetterOperator.return_value
        for kind in ("Bool", "Double", "String"):
            getattr(setter, "CreateWriting" + kind + "AttributesObject").side_effect = (
                lambda: create("Attribute"))
        self.uploader = MODULE.Arnold5XUploader()
        self.uploader.Initialize(self.operator)

    def parse(self, text, name="C:\\Programs\\part.iso"):
        source = io.StringIO(text)
        source.name = name
        self.uploader.ParseFile(self.operator, source)
        return self.created.get("CreateEmptyMotion", [])

    @staticmethod
    def events(motion):
        return motion.SetEventsBefore.call_args.args[0]

    @staticmethod
    def attributes(event):
        return {call.args[0].GetName(): call.args[0].SetValue.call_args.args[0]
                for call in event.AddAttribute.call_args_list}

    @staticmethod
    def position(motion):
        return motion.SetPosition.call_args.args[0]

    def assert_vector(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=10)

    def test_complete_sample(self):
        sample = Path(__file__).resolve().parent / "fixtures/PRG001.iso"
        motions = self.parse(sample.read_text(), str(sample))
        self.assertEqual(self.uploader._program.GetName(), "PRG001")
        self.assertEqual(list(self.uploader._groups), [f"GRP{number:03d}" for number in range(1, 7)])
        self.assertEqual(self.uploader._operation_count, 6)
        self.assertTrue(all(group.AddOperation.call_count == 1 for group in self.uploader._groups.values()))
        self.assertEqual(len(motions), 60)
        circular = [motion for motion in motions if motion.SetViaPosition.called]
        self.assertEqual(len(circular), 17)
        via = circular[0].SetViaPosition.call_args.args[0]
        self.assertEqual(via.SetProcessType.call_args.args[0], MODULE.ProcessType.ViaPoint)
        self.assert_vector(via.SetXYZ.call_args.args[0], (-0.080099, -0.403075, -0.737164))
        position = self.position(motions[13])
        self.assertEqual(position.SetProcessType.call_args.args[0], MODULE.ProcessType.ProcessCurve)
        self.assert_vector(position.SetXYZ.call_args.args[0], (-0.197437, -0.415687, -0.735082))
        self.assertEqual(position.SetOrientation.call_args.args[0], (0.0, 9.333, 89.974))
        events = [event for motion in motions for event in self.events(motion)]
        names = [event.GetName() for event in events]
        self.assertEqual(names.count("LaserOn"), 3)
        self.assertEqual(names.count("LaserOff"), 3)
        shapes = [event for event in events if event.GetName() in ("CIRCLE", "SLOT", "RECTANGLE")]
        self.assertEqual([event.GetName() for event in shapes], ["CIRCLE", "SLOT", "RECTANGLE"])
        circle, slot, rectangle = [self.attributes(event) for event in shapes]
        self.assertAlmostEqual(circle["Radius"], 0.0095)
        self.assertAlmostEqual(circle["LC_LD_REGSHAPE_ROTATION"], 45.403)
        direction_names = (
            "LC_LD_REGSHAPE_DIRX_X",
            "LC_LD_REGSHAPE_DIRX_Y",
            "LC_LD_REGSHAPE_DIRX_Z",
        )
        direction_z_names = (
            "LC_LD_REGSHAPE_DIRZ_X",
            "LC_LD_REGSHAPE_DIRZ_Y",
            "LC_LD_REGSHAPE_DIRZ_Z",
        )
        for shape in (circle, slot, rectangle):
            self.assertTrue(all(name in shape for name in direction_names))
            self.assertAlmostEqual(sum(shape[name] ** 2 for name in direction_names), 1.0)
            self.assertTrue(all(name in shape for name in direction_z_names))
            self.assertAlmostEqual(sum(shape[name] ** 2 for name in direction_z_names), 1.0)
        self.assert_vector(
            tuple(circle[name] for name in direction_names),
            (-0.7117483439371599, 0.6931445483082791, -0.11386364675884157),
        )
        self.assert_vector(
            tuple(circle[name] for name in direction_z_names),
            (0.0000735913994434, 0.1621721656595746, 0.9867624756087908),
        )
        self.assertAlmostEqual(slot["Length"], 0.03)
        self.assertAlmostEqual(slot["Radius"], 0.005)
        self.assertAlmostEqual(rectangle["Width"], 0.005999)
        self.assertAlmostEqual(rectangle["Radius"], 0.0015)
        self.assertAlmostEqual(rectangle["LC_LD_REGSHAPE_ROTATION"], -14.106)
        for motion in motions:
            if any(event.GetName() in ("CIRCLE", "SLOT", "RECTANGLE") for event in self.events(motion)):
                previous = motions[motions.index(motion) - 1]
                self.assertEqual(self.position(motion).SetProcessType.call_args.args[0], MODULE.ProcessType.ProcessPoint)
                self.assertEqual(self.position(motion).SetXYZ.call_args, self.position(previous).SetXYZ.call_args)
        tail = self.attributes(motions[-1].AddEventAfter.call_args.args[0])
        self.assertIn("M30", tail["Text"])

    def test_default_structure_modal_axes_and_event_order(self):
        motions = self.parse("N10 M51\nN20 T1\nN30 G00 X10 B-4 C90\nPP3\n;note\nG01 Y20 F166\n")
        self.assertEqual(self.uploader._program.GetName(), "part")
        self.assertEqual(self.uploader._group.GetName(), "GRP001")
        self.assertEqual(self.uploader._operation.GetName(), "OP001")
        self.assertEqual(self.uploader._group.AddOperation.call_count, 1)
        self.assertEqual(motions[0].SetMotionType.call_args.args[0], MODULE.MotionType.Pt2Pt)
        first = self.attributes(self.events(motions[0])[0])
        self.assertEqual(first["Text"], "N10 M51\nN20 T1")
        self.assertTrue(first["IsMultiLine"])
        self.assertEqual([event.GetName() for event in self.events(motions[1])], ["LaserOn", "TextEvent", "Speed"])
        self.assert_vector(self.position(motions[1]).SetXYZ.call_args.args[0], (0.01, 0.02, 0.0))
        self.assertEqual(self.position(motions[1]).SetOrientation.call_args.args[0], (0.0, -4.0, 90.0))
        self.assertEqual(self.attributes(self.events(motions[1])[-1])["Value"], 0.166)

    def test_same_group_and_missing_markers(self):
        self.parse(";START OF group - first\nG01 X1\n;END OF group - first\n"
                   ";START OF group - second\nG01 Y2\n;END OF group - second\nG01 Z3\n")
        self.assertEqual(len(self.uploader._groups), 1)
        self.assertEqual(self.uploader._group.AddOperation.call_count, 3)

    def test_modal_motion_and_reset_between_files(self):
        motions = self.parse("G01 X1 F2\nY3\nG01\nZ4\n")
        self.assertEqual(len(motions), 3)
        self.assert_vector(self.position(motions[-1]).SetXYZ.call_args.args[0], (0.001, 0.003, 0.004))
        self.parse("G01 Y5\n", "other.iso")
        self.assertEqual(self.uploader._motion_count, 1)
        self.assertEqual(self.uploader._axes["X"], 0.0)
        self.assertEqual(self.uploader._program.GetName(), "other")

    def test_unknown_command_not_misread_as_motion(self):
        motions = self.parse("G01 X1\nG123 X99\nG01 Y2\n")
        self.assertEqual(len(motions), 2)
        self.assertEqual(self.uploader._axes["X"], 1.0)
        self.assertEqual(self.attributes(self.events(motions[1])[0])["Text"], "G123 X99")

    def test_invalid_arc_reports_line(self):
        with self.assertRaisesRegex(ValueError, "line 2.*CIP requires"):
            self.parse("G01 X1\nCIP I1=1 J1=2 X2\n")

    def test_invalid_cycle_does_not_invent_dimensions(self):
        with self.assertRaisesRegex(ValueError, "Missing Regshape"):
            self.parse("G01 X1\nZYCL_KR\n")

    def test_events_without_motion_warn(self):
        self.parse("M30\n")
        self.operator.GetLogOperator.return_value.LogWarning.assert_called_once()
        self.assertFalse(self.created.get("CreateEmptyOperation"))


if __name__ == "__main__":
    unittest.main()