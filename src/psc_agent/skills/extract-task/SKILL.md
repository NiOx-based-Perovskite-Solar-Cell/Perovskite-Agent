---
name: extract-task
description: Build workbook rows for every device of a new paper — device map first, then rows per sheet
roles: [mapper, extract]
order: 60
---
You turn a paper into database rows, one device (sample) at a time.

Devices (the paper map): list every device the paper fabricates and reports results for: the control
(reference, unmodified) and each modified variant. Every concentration or treatment level is its own device.
Name them S1, S2, ... in the order the paper introduces them, control first, and give each its layer stack as
Layer rows in the fixed slots of workbook-schema (only the slots the device uses).

Rows: for the sheets you are asked for, give rows for every device. Steps shared by several devices (e.g. the same
perovskite recipe) are repeated for each device that uses them. Fill only what the paper states for that device.
A value the paper does not give is null, never a typical value. Recipes (spin speeds, temperatures, amounts) and
suppliers are often only in the supporting information: pages marked "(supporting information)" are that SI, use
them and cite their page. When no such pages are given, the SI is not available: then give no row for what only it holds. Use the exact column names of workbook-schema and
follow evidence-rules for every row.

- Vender: one row per chemical or material whose supplier or purity the paper names.
- Materials: one row per ingredient of each layer's solution or source (precursors, additives, dopants, the
  substrate), with its Type, the Solvent (mixture and ratio) and the Amount with its Unit, LayerID = the slot.
- Process: one row per step in the order it is done, per slot, with StepOrder 1, 2, ... within the slot. Put every
  number of the step in Parameter and the units in Unit, in the same order.
- Properties: every measured value the paper reports for the device, each with its unit: the champion and average
  PCE, Voc, Jsc and FF (for scan directions use the suffix), then every mechanism result. The names in
  workbook-schema are how to name a value, not a limit on what to take. Work through it in this order:
  1. Every table of the main text and of the SI, every row of it that is a device (or a film of that device's
     stack), every numeric column: one Properties row per cell. A fitting table gives all its parameters, not only
     the headline one: TRPL t1, A1, t2, A2 and the average; TPV and TPC the same; EIS Rs, Rrec and Crec.
  2. Every sentence of the results with a number for a device: band positions (VBM, CBM, work function, Fermi
     level) and the valence band offset to the perovskite (one sentence often lists it for every HTL), Vbi, J0, ideality factor, trap density, VTFL, mobility, conductivity, contact angle, roughness, grain
     size, band gap, hysteresis index, EQE-integrated Jsc, stabilized (SPO) PCE.
  3. Numbers printed inside a figure (an inset table, a bar label) count as printed values, with that figure as
     evidence.
  A measurement on a film or half-stack (e.g. NiOx/perovskite for TRPL, a bare HTL for UPS or contact angle) belongs to
  the device that has those layers; say which stack it was in Remarks (e.g. "HTL/PSK half-stack, Table S1"). The same
  quantity measured by two techniques is two rows, the technique in Remarks. A value read off a plot has source
  "figure" and a note on what the graph shows (read-figures): it is an estimate that a person reads again.
  Values of a molecule or calculation (DFT dipole moment, interaction energy) are not device properties: leave them out.
  Give a value a listed name only when it is that quantity: Trap Density is the defect density from SCLC (cm-3), not a
  surface density of adsorbed molecules (name that one as the paper does, e.g. "Adsorption density", molecules cm-2).
  When unsure, use the paper's own name.
  Go through the devices of the map one by one, the in_si ones too: a device with a single reported number still
  gets its row. Match the stack the sentence names in full: "the MeO-2PACz device" is the device whose paper_name is
  MeO-2PACz, not NiOx/MeO-2PACz.
- Stability: one row per test condition and device: the Environment, Encapsulation, Temperature, Humidity, the Time
  and the Retention reached at that time.

Example of rows (invented numbers, not from any paper), for a control S1 and a device S2 treated with PEAI:

    Layer    S2  {"LayerID": "3", "LayerType": "HTL treatment", "Material": "PEAI"}
    Layer    S2  {"LayerID": "4", "LayerType": "PSK", "Material": "Cs0.05FA0.85MA0.10PbI3", "Thickness (nm)": "500"}
    Process  S2  {"LayerID": "3", "StepOrder": "1", "ProcessType": "SpinCoating", "Descriptions": "PEAI in IPA",
                  "Parameter": "4000, 30", "Unit": "RPM, s"}
    Process  S2  {"LayerID": "4", "StepOrder": "3", "ProcessType": "Annealing", "Parameter": "100, 10", "Unit": "C, min"}
    Properties S2 {"Properties": "Champion PCE", "Values": "22.5", "Unit": "%"}
    Properties S2 {"Properties": "Average PCE", "Values": "21.8", "Errors": "0.4", "Unit": "%"}
    Stability S2 {"Environment": "N2", "Encapsulation": "N", "Time (hr)": "1000", "Retention (%)": "92"}
