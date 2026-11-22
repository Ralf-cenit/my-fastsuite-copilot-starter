---
description: "Use when editing or testing the Arnold5X ISO uploader, NC parsing, and CAM program creation."
applyTo: "OLPTranslators/Arnold/Arnold5X_ul.py,OLPTranslators/Arnold/tests/**/*.py"
---

# Arnold5X Uploader

## Layout And Runtime
- Standalone uploader: `OLPTranslators/Arnold/Arnold5X_ul.py`.
- Uploader class: `Arnold5XUploader(Uploader)` from `cenpyupload`.
- Required module declaration: `UPLOAD_CLASS_NAME = "Arnold5XUploader"`.
- Set `sys.dont_write_bytecode = True` and use the standard E2 uploader
  lifecycle: `Initialize()`, `ParseFile()`, and `Finalize()`.
- Reset per-file parser state in `ParseFile()` so coordinates, operations,
  events, and cycle parameters do not leak into another imported program.
- Do not depend on another translator project or import the downloader.
- Create CAM objects through the upload operator's `CreateEmpty*()` methods.
  Programs created through `CreateEmptyProgram()` are tracked by the operator;
  do not invent an additional program-registration API.

## Input And Program Structure
- Read an Arnold5X NC file, normally with the `.iso` extension.
- Name the main CAM program after the input filename without its directory or
  extension: `PRG001.iso` becomes `PRG001`.
- Optional leading block numbers such as `N170` do not affect parsing.
- Ignore blank lines. Recognize commands case-insensitively.
- Parse `;START OF <group-name> - <operation-name>` as the start of an
  operation in the named operations group.
- Parse `;END OF ...` as an operation-end marker, not a motion or TextEvent.
- Reuse an existing group when another operation has the same group name.
- Before adding the first motion, ensure a program, operations group, and
  operation exist and are linked using `AddOperationGroup()`, `AddOperation()`,
  and `AddMotion()`.
- If no operation marker precedes a motion, create a default group and
  operation. Current defaults are `GRP001` and sequential `OP001`, `OP002`, etc.
- A motion after an operation-end marker must open a new operation if no
  explicit start marker has done so.

## Linear And Point-To-Point Motions
Example:

```text
N180 G01 X-92.014 Y-400.256 Z-656.553 B9.333 C89.974 F440.0
```

- `G00` or `G0` selects `MotionType.Pt2Pt`.
- `G01` or `G1` selects `MotionType.Linear`.
- Use `MotionType.Pt2Pt` for point-to-point motion; `MotionType.Joint` does not
  exist. Keep test doubles consistent with the host enum.
- Use `TargetType.Cartesian` for these coordinate targets, including G00.
- Use `ProcessType.Auxiliary` for point-to-point targets and
  `ProcessType.ProcessCurve` for linear contour targets.
- Coordinates, orientation values, feedrate, and the motion command are modal:
  omitted values retain their last parsed value. A command-only block does
  not create a duplicate motion.
- `X`, `Y`, and `Z` are Cartesian coordinates in millimeters. Divide by 1000
  before calling `SetXYZ()` because CAM positions use meters.
- `A`, `B`, and `C` correspond to orientation components `Rx`, `Ry`, and `Rz`.
  Preserve angles in degrees and retain omitted components.
- Support coordinate words with or without `=`, for example `X10` and `X=10`.
- An `F` value on a motion creates a `Speed` event with
  `InsertPosition.InsertBefore` on that motion. Convert mm/s to m/s by dividing
  by 1000, matching the Arnold5X downloader's convention, not mm/min.
- The Speed event uses `Value` and `PathType` attributes. Use `"Contour"` for
  contour motion and the existing `"Joint"` PathType string for G00; this
  string is not a MotionType enum member.

## Circular Motions
Example:

```text
N230 CIP I1=-80.099 J1=-403.075 K1=-737.164 X-98.576 Y-399.1 Z-737.816 B9.333 C89.974
```

- `CIP` selects `MotionType.Circular`.
- `I1`, `J1`, and `K1` are absolute Cartesian via-point coordinates in
  millimeters, not circle-center offsets. All three are required for an arc.
- `X`, `Y`, and `Z` describe the endpoint; omitted endpoint and orientation
  components retain their modal values.
- Create an endpoint with `ProcessType.ProcessCurve` and a via position with
  `ProcessType.ViaPoint`. Both use `TargetType.Cartesian`.
- Attach the via position using `SetViaPosition()` and convert both positions
  from millimeters to meters.
- Do not silently interpret unsupported `G02`/`G03` commands as CIP arcs.

## Laser And Text Events
- `PP3` creates a `LaserOn` event.
- `PP6` creates a `LaserOff` event.
- Buffer these events in source order and attach them before the next motion.
- Lines that are neither defined motions, structural markers, cycle
  instructions, nor recognized events become TextEvents. Preserve their text.
- Collect consecutive unknown lines into one multiline TextEvent.
- TextEvents use the attributes `Text`, `IsMultiLine`, `MultiLineSeparator`,
  and `IsComment`. The uploader currently uses a newline separator and
  `IsComment = False` to preserve arbitrary NC text.
- Flush accumulated text before queuing a recognized event so mixed text,
  laser, and speed events retain their original ordering.
- Attach buffered TextEvents to the next motion with
  `InsertPosition.InsertBefore`, including file-header text before the first
  motion. Do not discard text at an operation boundary.
- At end of file, preserve remaining events as `InsertPosition.InsertAfter`
  on the last motion, because no next motion exists.
- If a file has pending events but no motions, log a warning rather than
  inventing a motion solely to carry them.

## Regshape Cycles
- Regshape position and orientation are those of the last parsed motion,
  not the following retract motion and not the subsequent `ROT ...0.0` reset.
- Create a linear reference motion at that position with
  `ProcessType.ProcessPoint`, and attach the shape event before it.
- `ROT Y... Z...` repeats the last motion's orientation; it is not a move.
- `G91` and `G90` in the documented cycle sequences are control scaffolding;
  ignore them and do not change the modal Cartesian position.
- A standalone cycle `F` line does not create another motion Speed event.
  It may supply `RegshapeCuttingFeedrate`, converted from mm/s to m/s.
- `AROT Z...` gives the rotation in the working plane. To invert the existing
  downloader's `_e2Rotation()` convention, set `LC_LD_REGSHAPE_ROTATION` to
  the NC angle plus 90 degrees when the last B angle is nonnegative, or minus
  90 degrees when B is negative.
- Also set `LC_LD_REGSHAPE_DIRX_X`, `LC_LD_REGSHAPE_DIRX_Y`, and
  `LC_LD_REGSHAPE_DIRX_Z` to the components of the Regshape local X axis
  transformed by `Rz(C) * Ry(B) * Rx(A) * Rz(LC_LD_REGSHAPE_ROTATION)`.
  The resulting direction is a unit vector in world coordinates.
- Also set `LC_LD_REGSHAPE_DIRZ_X`, `LC_LD_REGSHAPE_DIRZ_Y`, and
  `LC_LD_REGSHAPE_DIRZ_Z` to the components of the Regshape local Z axis
  transformed by `Rz(C) * Ry(B) * Rx(A)`. Rotation around the local Z axis
  does not change this direction; the resulting vector is unit length. With
  angles in degrees converted to radians for the trigonometric functions, use:

  ```text
  DIRZ_X = cos(C) * sin(B) * cos(A) + sin(C) * sin(A)
  DIRZ_Y = sin(C) * sin(B) * cos(A) - cos(C) * sin(A)
  DIRZ_Z = cos(B) * cos(A)
  ```
- Shape dimensions are in millimeters in NC and meters in CAM.
- Use `ExplodeRegshape = False` and reset collected cycle parameters after
  each completed cycle.

### Circle
```text
ROT Y9.333 Z89.974
G91
F166.7
R4=19.0
AROT Z-44.597
ZYCL_KR
ROT Y0.0 Z0.0
G90
```

- `ZYCL_KR` creates a `CIRCLE` event.
- `R4` is the diameter; set `Radius = R4 / 2000`.

### Slot
```text
ROT Y0.084 Z93.898
G91
F166.7
R1=30.0
R2=10.0
R3=5.0
AROT Z59.135
ZYCL_RQ
ROT Y0.0 Z0.0
G90
```

- For `ZYCL_RQ`, `R1` is length, `R2` is width, and `R3` is corner radius.
- When `2 * R3 == R2`, within numerical tolerance, create a `SLOT` event.
- Set `Length = R1 / 1000` and `Radius = R3 / 1000`.

### Rectangle
```text
ROT Y-4.127 Z104.025
G91
F166.7
R1=21.004
R2=5.999
R3=1.5
AROT Z75.894
ZYCL_RQ
ROT Y0.0 Z0.0
G90
```

- When `2 * R3 < R2`, create a `RECTANGLE` event.
- Set `Length = R1 / 1000`, `Width = R2 / 1000`, and `Radius = R3 / 1000`.
- Reject missing required dimensions, nonpositive length/width/diameter,
  negative corner radius, or a corner diameter larger than the width.
- Reject a Regshape cycle without a preceding motion in the current operation.

## Validation And References
- Original parsing notes:
  `docs/ctrl-specific/Arnold/spec/Arnold5X_Uploader.txt`.
- Complete machine-program fixture:
  `OLPTranslators/Arnold/tests/fixtures/PRG001.iso`.
- Regression tests: `OLPTranslators/Arnold/tests/test_Arnold5X_ul.py`.
- Run with the selected Python interpreter:

  ```text
  python -B -m unittest discover -s OLPTranslators/Arnold/tests -p test_Arnold5X_ul.py -v
  ```

- The fixture produces the main program `PRG001`, six operations groups with
  one operation each, 57 NC motions including 17 CIP arcs, and three additional
  Regshape reference motions: 60 motions in total.
- Verify modal coordinates, event ordering, filenames, default structure,
  per-file state reset, cycle dimensions, and malformed-input diagnostics.
- Report malformed recognized motions/cycles with the source line number.
  Unknown commands must remain text rather than silently changing axes.
- Native FASTSUITE modules may be unavailable outside the CAM host even when
  Python package discovery succeeds. API-double tests verify parser behavior,
  not native CAM integration; state that limitation when reporting validation.
- Keep test doubles consistent with verified host API names. Do not treat
  passing tests against invented enum members as proof of host compatibility.