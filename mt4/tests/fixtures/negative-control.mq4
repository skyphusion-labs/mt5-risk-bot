//+------------------------------------------------------------------+
//| negative-control.mq4                                             |
//|                                                                  |
//| THIS FILE MUST NEVER COMPILE. It is not an Expert Advisor, it is  |
//| not deployable, and it must never be copied into a terminal's     |
//| MQL4/Experts directory.                                          |
//|                                                                  |
//| .github/workflows/mt4-compile.yml compiles it on every run and   |
//| FAILS if MetaEditor produces an .ex4 from it, or if the compile   |
//| log reports zero errors. That is what makes the artifact         |
//| assertion on the real Expert mean something: MetaEditor exits 0   |
//| on a FAILED compile and 1 on a SUCCESSFUL one (measured on       |
//| windows-latest 2026-09-26, straightedge#86), so the exit code is  |
//| not a gate and the only trustworthy signals are the artifact and  |
//| the log counts. A guard nobody has watched fail is not a guard;   |
//| this file is how it is watched failing on every single run.      |
//|                                                                  |
//| Do NOT fix the syntax error below. Fixing it silently turns the  |
//| gate green forever.                                              |
//+------------------------------------------------------------------+
#property strict

int OnInit() { this is not MQL4 ;
