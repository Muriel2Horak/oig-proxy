# Inverter solar-priority labels

`tbl_invertor_prms.PRRTY` describes PV energy distribution priority (`PV_PRTY`
in the CBB Modbus reference). It is separate from BOX operating mode.

| Raw value | Czech display label | Inverter command |
| --- | --- | --- |
| 0 | Baterie, zátěž, síť | `^S006SEP00` |
| 1 | Zátěž, baterie, síť | `^S006SEP01` |
| 2 | Zátěž, síť, baterie | `^S006SEP02` |

Source: [Voltronic Modbus Protocol for P17](https://ftps.voltronic.com.tw/V/ViewPower-C76A89B88C10454AA513/SolarPowerPro/SolarPowerPro1.07/Manual/Modbus%20protocol%20for%20P17.pdf),
register `0x0315`, Note 1 (printed page 11). The manufacturer maps ASCII
`00/01/02` to battery-load-grid, load-battery-grid and load-grid-battery.
The source workbook `Databaze!K344` identifies PRRTY, `M344` identifies `S006SEP`,
and `N344` contains the older, misleading descriptions. The archived workbook
is preserved as source evidence; runtime labels come from `sensor_map.json`.

## Mode changes and experimental controls

The BOX sends inverter SEP priority according to its selected operating mode.
A manual priority change will likely last only until the next mode change.
Do not use a manual selection as a persistent override of the mode profile.

Public add-on 2.1.3 corrects the read-only sensor's labels and retains existing
write permissions. Inverter PRRTY is not on the public write whitelist.
Existing private development builds also have an experimental select companion;
its `option_labels`, overlaid `sensor_metadata.enum_map` and `description_cs`
must use these same labels and the following help text:

> Box při každém přepnutí režimu pošle do střídače příkaz SEP podle režimu.
> Ruční změna priority proto nejspíš vydrží jen do další změny režimu.

Keep `allowed_values=[0,1,2]`, `experimental=true`, the `_cfg` suffix, raw numeric
state/commands and all entity/unique IDs unchanged. This label release does not
publish or enable the private development controls.

## Capture verification before the label change

Read-only analysis of November 5–6, 2026 captures compared 127 BOX-origin
`tbl_invertor_prms` readbacks with the latest preceding inverter SEP ACK event.
It required matching device-clock segments and ACK source time no later than
the table source time. Two clock discontinuities included timestamps in 1970;
cross-segment associations were excluded.

- 119 usable readbacks reported PRRTY 1 after SEP01, referencing 17 distinct ACKs.
  Repeated tables are not independent controlled tests.
- Eight readbacks had no usable preceding SEP event in their clock segment.
- No table arrived during the captured SEP00 interval before SEP01 was restored.
- One direct PRRTY 2 request was followed by SEP02, but the export ended before
  a subsequent PRRTY table readback.
- A receive-order-only apparent SEP01/PRRTY0 mismatch crossed a clock reset.

The capture therefore supports the value 1 correspondence but **does not confirm
complete PRRTY 0/1/2 to SEP00/01/02 readback equivalence**. A BOX-reported table
and a BOX-reported inverter ACK are also not independent inverter register
readbacks. Labels are corrected using the manufacturer mapping and the supplied
operational evidence; no wire-value translation is introduced.
