---
description: "Use when editing the Arnold2DS ISO downloader and its program output."
applyTo: "OLPTranslators/Arnold/Arnold2DS.py"
---

# Arnold2DS Downloader

## Layout And Runtime
- Folder: `OLPTranslators/Arnold/`.
- Standalone downloader: `OLPTranslators/Arnold/Arnold2DS.py`.
- Downloader class: `Arnold2DS(Downloader)`.
- Required module declaration: `DOWNLOAD_CLASS_NAME = "Arnold2DS"`.
- Set `sys.dont_write_bytecode = True` and use the standard E2 downloader lifecycle.
- Do not depend on another translator project.

## Output
- Generate one `<E2-programname>.iso` file in the controller output directory.
- Capture the active E2 program name in `Initialize()` and use that same name
  for the filename and program header.
- Write lines through `FileUtility.AppendTextToFile()` without appending a line
  terminator manually; the utility supplies it. Extra terminators create blank
  lines between output blocks. Preserve the two intentional blank lines after
  the separator following `TRANS X=0.0 Y=0.0` and the blank line before the
  program-end sequence.
- Register the output with `operator.AddOutputFilePath()` in `CloseOutputFile()`.
- `self._blockNumber` is the optional integer numbering increment, default `0`.
  When greater than zero, prefix every nonblank, unsuppressed output line with
  `N<number>` and increase the number by this increment. Blank lines are written
  unchanged and do not advance the count; numbering restarts at initialization.
- `ProgramStart()` writes this header exactly; motion blocks follow the two blank
  lines in the same file.

```text
%_N_[E2-programname]_MPF
;$PATH=/_N_WKS_DIR/_N_[E2-programname]_WPD
MAT_
back:
READY_TABLE
G90 G64 SOFT
T1
G0 Z=0
G54
;--------------
TRANS X=0.0 Y=0.0
;--------------


```

## Program End
`ProgramEnd()` writes these final lines after the generated motion blocks:

```text

;--------------
CHANGE_TABLE     ;start the changing of the tables
GOTOB back
M30
```

## Subprogram Start
Subprogram calls remain at their original positions in the main program, before
`_subprogramActive` is set. While the flag is true, `_writeLine()` appends
unsuppressed lines to the instance buffer `_subprogramLines` instead of writing
them to the file. Keep the flag true through the entire subprogram footer.
`ProgramEnd()` writes the main footer ending in `M30`, then flushes and clears
the buffer. Assign block numbers only when lines are written to the file, so
numbering follows final output order. Reset the buffer and flag in `Initialize()`.
This requires subprogram processing to finish before the final main `ProgramEnd()`.

`SubProgramStart()` gets the subprogram identifier from the incoming `program`
parameter's `GetName()` and writes these lines:

```text


%_N_[subprogramnumber]_SUB_SPF
;$PATH=/_N_WKS_DIR/_N_[subprogramnumber]_WPD
G90
```

## Subprogram End
`SubProgramEnd()` gets the subprogram identifier from the incoming `program`
parameter's `GetName()` and writes these final lines:

```text
;END OF GENERATION OF : [subprogramnumber]
G90
PP7
M17
```

## Motion And Events
- Preserve existing motion and event behavior unless explicitly requested.
- Output comments start with `;`.
- `OperationGroupStart()` stores the incoming group's name; `OperationGroupEnd()`
  clears it. Neither callback writes a comment.
- `OperationStart()` and `OperationEnd()` write `;START OF <group-name> - <operation-name>`
  and `;END OF <group-name> - <operation-name>`, respectively.
- `LaserOn` writes `PP3` on its own output line.
- `LaserOff` writes `PP6` on its own output line.
- An event named `CIRCLE` calls `CircleRegshape()` and reads the double event
  attribute `Radius`. Use the current motion's
  `GetPosition().GetXYZ()` for X/Y. Convert X/Y and radius from meters to
  millimeters and format them with the existing three-decimal coordinate
  formatter. Write these lines without rotation or G90/G91:

  ```text
  ; START OF :CIRCLE
  HOLEPOS_X  =[currentxcoordinate]
  HOLEPOS_Y  =[currentycoordinate]
  HOLE_RADIUS=[circleradius]
  CUT_HOLE
  ; END OF :CIRCLE
  ```

- `SLOT` and `RECTANGLE` dispatch calls are currently commented out in
  `HandleEvent()`. Keep the methods available but do not invoke them there.
- The retained `SlotRegshape()` method reads the double event
  attributes `Length`, `Width`, `Radius`, `LC_LD_REGSHAPE_ROTATION`, and
  `RegshapeCuttingFeedrate`. It writes:

  ```text
  ROT Y<A-angle> Z<C-angle>  (example for A->B, C->C; only mapped axes)
  G91
  F<RegshapeCuttingFeedrate>
  R1=<Length>
  R2=<Width>
  R3=<Radius>
  AROT Z<LC_LD_REGSHAPE_ROTATION>
  ZYCL_RQ
  ROT Y0.0 Z0.0  (same mapped addresses as the initial ROT line)
  G90
  ```

- The retained `RectangleRegshape()` method reads
  the same double attributes as `SLOT` (`Length`, `Width`, `Radius`,
  `LC_LD_REGSHAPE_ROTATION`, and `RegshapeCuttingFeedrate`) and writes the same
  register and `ZYCL_RQ` sequence, with mapped current-angle and reset ROT lines.
- All three regshape methods read the boolean `ExplodeRegshape`. If it is false,
  set `_suppressMotion` and skip subsequent motion output. Reset that flag on a
  `RegshapeRetract` event or at operation end. Events attached to suppressed
  motions must still be processed so the retract can clear the flag. Whenever
  suppression is cleared, reset modal address state so the next motion emits
  all coordinate and orientation addresses.
- When `_suppressMotion` is true, `_writeLine()` suppresses all output.
- In all three regshape methods, multiply `Length`, `Width`, and `Radius` values
  by 1000 to convert meters to millimeters. Format those values, rotation, and
  regshape feedrate with the existing three-decimal formatter; rotation and
  feedrate are not unit-scaled.

- For `TextEvent`, output the `Text` string prefixed with `;` only when its
  `IsComment` boolean attribute is true; otherwise emit no text-event line.
- Preserve before/after-motion event order and process motions attached to events.
- Convert positions and circular via points from meters to millimeters (x1000).
- Convert contour speeds from m/s to integer mm/s (x1000).
- Read the global program enum attribute `CIRCLE_OUTPUT` with
  `program.GetLiteral("CIRCLE_OUTPUT", False)` during initialization. Set
  `self.CIPCircle` to `True` only when its string value is `"CIP"`; otherwise
  use `False` (`"G2G3"` selects standard circular output).
- Emit `G00` for joint motions, `G01` for linear motions, and `G02`/`G03` for arcs.
- `self._rotationAxisMap` maps world rotation components to controller output
  addresses: `A=Rx`, `B=Ry`, `C=Rz`. Use `None` for an unavailable axis. For
  example, `{"A": "B", "B": "C", "C": "A"}` remaps the controller addresses.
  Motion A/B/C axes are emitted only on CIP circular moves; other motions ignore
  them. The retained slot/rectangle ROT lines use the current motion's
  orientation and map controller A/B/C to ROT X/Y/Z. For example, world A mapped
  to controller B emits its angle on ROT Y. Its reset ROT line uses the same
  mapped addresses with each value set to `0.0`.
- For `G02`/`G03`, calculate the 3D circle center from the last executed endpoint,
  the current via point, and the current endpoint. Output incremental center
  offsets in millimeters: `I = center-X - start-X` and
  `J = center-Y - start-Y`. Do not output `K` for G2/G3 circles. Never use
  absolute via coordinates for `I/J`.
- Coincident or collinear arc points cannot define a unique center; raise an
  error rather than emitting incorrect center offsets.
- Set `self.CIPCircle` to `False` for `G02`/`G03`, or `True` to output circular
  motions as `CIP I1=<via-X> J1=<via-Y> K1=<via-Z> X<end-X> Y<end-Y>
  Z<end-Z> A<angle-A> B<angle-B> C<angle-C> F<feedrate>`.
- Every CIP block outputs all coordinates and enabled angles, even when unchanged.
  Feedrate `F` is modal: emit it only when its formatted value changes or modal
  state is reset. Via coordinates
  use absolute millimeters and always have `I1=`, `J1=`, and `K1=` prefixes.
  While `self.CIPCircle` is `True`, coordinate and orientation addresses use
  an empty separator for all motion types, overriding `COORDINATE_SEPARATOR`.
  Non-reference CIP moves update endpoint/orientation/feedrate modal state;
  reference CIP moves remain commented out and do not update state.
- `COORDINATE_SEPARATOR` defaults to `"="`; set it to `""` for coordinates without
  an equals sign. Apply it to `X/Y/Z`, `I/J/K`, and `A/B/C` for every motion type,
  including reference motions, when `self.CIPCircle` is `False`.
  Feedrate `F`, CIP via prefixes, and the fixed header are unchanged.
- Determine arc direction from the cross product dotted with the active base Z.
- Keep `PRECISION_COORD`, `PRECISION_ANGLE`, and `PRECISION_SPEED` at 3 decimal
  places; trim trailing zeros but retain one fractional digit.
- Preserve modal address suppression for non-CIP moves and reset it at each
  operation start. CIP moves suppress only unchanged feedrate `F` values.
- If modal suppression removes every address from a motion, omit the output
  line instead of writing a prefix-only block.
- Prefix reference motion blocks with `;`; do not commit their modal state.
- Retain the existing resource attribute names for compatibility:
  `CENOlpHonkyTonkPulses`, `CENOlpHonkyTonkGroupMap`,
  `CENOlpHonkyTonkSpeedUnit`, and `CENOlpSuppressConfig`.
- Leave unimplemented event callbacks unchanged.
  Do not introduce additional controller commands without a requirement.