# CSI API capability matrix

Generated 2026-11-26 14:02 by tools/gen_api_matrix.py from the type libraries installed on this PC. Full member list with signatures: [api/methods.csv](api/methods.csv); per library: api/<lib>.json; run-time probe: api/runtime_probe.json.

Methods per interface in each program's library, and whether the SapModel property that returns it answered at run time (blank model, hidden instance). A method present in SAFE's library is not proof SAFE implements it: check the run-time column and test before relying on it.

| interface | SapModel property | ETABS | SAP2000 | SAFE | runtime E / S / F |
|---|---|---|---|---|---|
| cAnalysisResults | Results | 37 | 40 | 37 | ok / ok / ok |
| cAnalysisResultsSetup |  | 21 | 28 | 21 |  |
| cAnalyze | Analyze | 21 | 21 | 21 | ok / ok / ok |
| cAreaElm | AreaElm | 13 | 18 | 13 | ok / ok / ok |
| cAreaObj | AreaObj | 64 | 77 | 64 | ok / ok / ok |
| cAutoSeastate |  |  | 3 |  |  |
| cAutoSeismic |  | 6 | 60 | 6 |  |
| cAutoWind |  |  | 75 |  |  |
| cCableObj | CableObj |  | 55 |  |  / ok /  |
| cCaseBuckling |  |  | 7 |  |  |
| cCaseDirectHistoryLinear |  | 1 | 11 | 1 |  |
| cCaseDirectHistoryNonlinear |  | 1 | 17 | 1 |  |
| cCaseExternalResults |  |  | 2 |  |  |
| cCaseHyperStatic |  | 3 | 3 | 3 |  |
| cCaseModalEigen |  | 9 | 9 | 9 |  |
| cCaseModalHistoryLinear |  | 3 | 18 | 3 |  |
| cCaseModalHistoryNonlinear |  | 1 | 20 | 1 |  |
| cCaseModalRitz |  | 7 | 7 | 7 |  |
| cCaseMovingLoad |  |  | 11 |  |  |
| cCasePowerSpectralDensity |  |  | 12 |  |  |
| cCaseResponseSpectrum |  | 16 | 24 | 16 |  |
| cCaseStaticLinear |  | 5 | 5 | 5 |  |
| cCaseStaticLinearMultistep |  |  | 7 |  |  |
| cCaseStaticNonlinear |  | 21 | 21 | 21 |  |
| cCaseStaticNonlinearMultistep |  |  | 5 |  |  |
| cCaseStaticNonlinearStaged |  | 29 | 30 | 29 |  |
| cCaseSteadyState |  |  | 12 |  |  |
| cCombo | RespCombo | 11 | 18 | 12 | ok / ok / ok |
| cConstraint | ConstraintDef | 4 | 24 | 4 | ok / ok / ok |
| cCoordSys | CoordSys |  | 10 |  |  / ok /  |
| cDAlAA_2015 |  |  | 4 |  |  |
| cDAlAA_2020 |  |  | 4 |  |  |
| cDAlAA_ASD_2000 |  |  | 4 |  |  |
| cDAlAA_LRFD_2000 |  |  | 4 |  |  |
| cDAlEurocode_9_2007 |  |  | 4 |  |  |
| cDCdAISI_16 |  |  | 4 |  |  |
| cDCdAISI_ASD96 |  |  | 4 |  |  |
| cDCdAISI_LRFD96 |  |  | 4 |  |  |
| cDCdEuroCold06 |  |  | 4 |  |  |
| cDCoACI318_08_IBC2009 |  | 4 | 4 | 4 |  |
| cDCoACI318_11 |  | 4 | 4 | 4 |  |
| cDCoACI318_14 |  | 4 | 4 | 4 |  |
| cDCoACI318_19 |  | 4 | 4 | 4 |  |
| cDCoACI318_25 |  | 4 | 4 | 4 |  |
| cDCoACI350_20 |  |  | 4 |  |  |
| cDCoAS_3600_09 |  | 4 | 4 | 4 |  |
| cDCoAS_3600_2018 |  | 4 |  | 4 |  |
| cDCoBS8110_97 |  | 4 | 4 | 4 |  |
| cDCoCSA_A23_3_04 |  |  | 4 |  |  |
| cDCoCSA_A23_3_14 |  |  | 4 |  |  |
| cDCoCSA_A23_3_19 |  |  | 4 |  |  |
| cDCoCSA_A23_3_24 |  |  | 4 |  |  |
| cDCoChinese_2002 |  |  | 4 |  |  |
| cDCoChinese_2010 |  | 4 | 4 | 4 |  |
| cDCoEurocode_2_2004 |  | 4 | 4 | 4 |  |
| cDCoEurocode_2_2023 |  | 4 | 4 | 4 |  |
| cDCoHong_Kong_CP_2013 |  | 4 | 4 | 4 |  |
| cDCoIndian_IS_456_2000 |  | 4 | 4 | 4 |  |
| cDCoItalianNTC2008C |  | 4 | 4 | 4 |  |
| cDCoKCI_1999 |  |  | 4 |  |  |
| cDCoKDS1420_2022 |  |  |  | 4 |  |
| cDCoMexican_RCDF_2004 |  | 4 | 4 | 4 |  |
| cDCoMexican_RCDF_2017 |  | 4 |  | 4 |  |
| cDCoNZS_3101_2006 |  | 4 | 4 | 4 |  |
| cDCoSP63133302011 |  | 4 | 4 | 4 |  |
| cDCoSingapore_CP_65_99 |  |  | 4 |  |  |
| cDCoTCVN5574_2018 |  | 4 | 4 | 4 |  |
| cDCoTS_500_2000 |  | 4 | 4 | 4 |  |
| cDCoTS_500_2000_R2018 |  | 4 |  | 4 |  |
| cDCompColAISC360_22 |  | 4 | 4 | 4 |  |
| cDCompColCSAS16_19 |  | 4 | 4 | 4 |  |
| cDCompColCSAS16_24 |  | 4 | 4 | 4 |  |
| cDCompColEurocode_4_2004 |  | 4 | 4 | 4 |  |
| cDCompColIS11384_2022 |  | 4 | 4 | 4 |  |
| cDCompColKDS143100_2022 |  | 4 | 4 | 4 |  |
| cDConcShellACI350_20 |  |  | 4 |  |  |
| cDConcShellEurocode_2_2004 |  | 4 | 4 | 4 |  |
| cDConcSlabACI318_14 |  | 1 |  | 1 |  |
| cDConcSlabACI318_19 |  | 1 |  | 1 |  |
| cDConcSlabACI318_25 |  | 1 |  | 1 |  |
| cDConcreteShellDesignRequest |  | 9 | 9 | 9 |  |
| cDStAISC360_05_IBC2006 |  | 4 | 4 | 4 |  |
| cDStAISC360_10 |  | 4 | 4 | 4 |  |
| cDStAISC360_16 |  | 4 | 4 | 4 |  |
| cDStAISC360_22 |  | 4 | 4 | 4 |  |
| cDStAISC_ASD89 |  | 4 | 4 | 4 |  |
| cDStAISC_LRFD93 |  | 4 | 4 | 4 |  |
| cDStAPI_4F_2020 |  |  | 4 |  |  |
| cDStAPI_RP2A_LRFD97 |  |  | 4 |  |  |
| cDStAPI_RP2A_WSD2000 |  |  | 4 |  |  |
| cDStAPI_RP2A_WSD2014 |  |  | 4 |  |  |
| cDStASCE_10_97 |  |  | 4 |  |  |
| cDStAustralian_AS4100_2020 |  | 4 | 4 | 4 |  |
| cDStAustralian_AS4100_98 |  | 4 | 4 | 4 |  |
| cDStBS5950_2000 |  | 4 | 4 | 4 |  |
| cDStCanadian_S16_09 |  | 4 | 4 | 4 |  |
| cDStCanadian_S16_14 |  | 4 | 4 | 4 |  |
| cDStCanadian_S16_19 |  | 4 | 4 | 4 |  |
| cDStCanadian_S16_24 |  | 4 | 4 | 4 |  |
| cDStChinese_2002 |  |  | 4 |  |  |
| cDStChinese_2010 |  | 4 | 4 | 4 |  |
| cDStChinese_2018 |  | 4 | 4 | 4 |  |
| cDStEN1993_1_1_2005 |  | 4 | 4 | 4 |  |
| cDStEN1993_1_1_2022 |  | 4 | 4 | 4 |  |
| cDStEurocode_3_2005 |  | 4 | 4 | 4 |  |
| cDStIndian_IS_800_2007 |  | 4 | 4 | 4 |  |
| cDStItalianNTC2008S |  | 4 | 4 | 4 |  |
| cDStItalianNTC2018S |  | 4 | 4 | 4 |  |
| cDStKDS143100_2022 |  | 4 | 4 | 4 |  |
| cDStNewZealand_NZS3404_97 |  | 4 | 4 | 4 |  |
| cDStNorsok_N0042013 |  |  | 4 |  |  |
| cDStSP16_13330_2011 |  | 4 | 4 | 4 |  |
| cDStSP16_13330_2017 |  | 4 | 4 | 4 |  |
| cDatabaseTables | DatabaseTables | 25 | 37 | 25 | ok / ok / ok |
| cDesignAluminum | DesignAluminum |  | 20 |  |  / ok /  |
| cDesignColdFormed | DesignColdFormed |  | 20 |  |  / ok /  |
| cDesignCompositeBeam | DesignCompositeBeam | 22 |  | 22 | ok /  / ok |
| cDesignCompositeColumn | DesignCompositeColumn | 22 |  | 22 | ok /  / ok |
| cDesignConcrete | DesignConcrete | 14 | 17 | 14 | ok / ok / ok |
| cDesignConcreteShell | DesignConcreteShell | 5 | 5 | 5 | ok / ok / ok |
| cDesignConcreteSlab | DesignConcreteSlab | 4 |  | 4 | ok /  / ok |
| cDesignForces |  | 5 | 3 | 5 |  |
| cDesignResults | DesignResults |  |  |  | ok / ok / ok |
| cDesignShearWall | DesignShearWall | 6 |  | 6 | ok /  / ok |
| cDesignSteel | DesignSteel | 24 | 26 | 24 | ok / ok / ok |
| cDesignStrip |  | 7 |  | 7 |  |
| cDetailing | Detailing | 49 |  | 49 | ok /  / ok |
| cDiaphragm | Diaphragm | 5 |  | 5 | ok /  / ok |
| cEditArea | EditArea | 1 | 7 | 1 | ok / ok / ok |
| cEditFrame | EditFrame | 1 | 7 | 1 | ok / ok / ok |
| cEditGeneral | EditGeneral | 1 | 11 | 1 | ok / ok / ok |
| cEditPoint | EditPoint |  | 6 |  | ok / ok / ok |
| cEditSolid | EditSolid |  | 1 |  |  / ok /  |
| cExternalAnalysisResults | ExternalAnalysisResults |  | 8 |  |  / ok /  |
| cFile | File | 8 | 9 | 8 | ok / ok / ok |
| cFrameObj | FrameObj | 74 | 102 | 74 | ok / ok / ok |
| cFunction | Func | 7 | 7 | 7 | ok / ok / ok |
| cFunctionPSD |  |  | 4 |  |  |
| cFunctionRS |  | 4 | 74 | 4 |  |
| cFunctionSS |  |  | 4 |  |  |
| cFunctionTH |  |  | 18 |  |  |
| cGenDispl | GDispl | 13 | 12 | 13 | ok / ok / ok |
| cGenRefLine | GenRefLine |  | 10 |  |  / ok /  |
| cGridSys | GridSys | 12 |  | 12 | ok /  / ok |
| cGroup | GroupDef | 8 | 8 | 8 | ok / ok / ok |
| cHelper |  | 11 | 11 | 11 |  |
| cLineElm | LineElm | 16 | 22 | 16 | ok / ok / ok |
| cLinkElm | LinkElm |  | 11 |  | ok / ok / ok |
| cLinkObj | LinkObj | 22 | 32 | 22 | ok / ok / ok |
| cLoadCases | LoadCases | 7 | 13 | 7 | ok / ok / ok |
| cLoadPatterns | LoadPatterns | 11 | 12 | 11 | ok / ok / ok |
| cMassSource | SourceMass |  | 8 |  |  / ok /  |
| cModifierArea |  |  | 6 |  |  |
| cModifierCable |  |  | 6 |  |  |
| cModifierFrame |  |  | 6 |  |  |
| cNamedAssign | NamedAssign |  |  |  |  / ok /  |
| cNamedDisplay | NamedDisplay |  | 5 |  |  / ok /  |
| cNamedSet | NamedSet |  | 7 |  |  / ok /  |
| cOAPI |  | 9 | 9 | 9 |  |
| cOptions | Options | 2 | 4 | 2 | ok / ok / ok |
| cPattern | PatternDef |  | 5 |  | ok / ok / ok |
| cPierLabel | PierLabel | 6 |  | 6 | ok /  / ok |
| cPlaneElm | PlaneElm |  | 15 |  |  / ok /  |
| cPluginCallback |  | 1 | 1 | 1 |  |
| cPluginContract |  | 2 | 2 | 2 |  |
| cPointElm | PointElm | 20 | 23 | 20 | ok / ok / ok |
| cPointObj | PointObj | 58 | 67 | 58 | ok / ok / ok |
| cPropArea | PropArea | 35 | 25 | 35 | ok / ok / ok |
| cPropAreaSpring | PropAreaSpring | 5 |  | 5 | ok /  / ok |
| cPropCable | PropCable |  | 8 |  |  / ok /  |
| cPropFrame | PropFrame | 105 | 103 | 105 | ok / ok / ok |
| cPropFrameSDShape |  | 13 | 45 | 13 |  |
| cPropLineSpring | PropLineSpring | 5 |  | 5 | ok /  / ok |
| cPropLink | PropLink | 39 | 41 | 39 | ok / ok / ok |
| cPropMaterial | PropMaterial | 46 | 59 | 46 | ok / ok / ok |
| cPropMaterialTD |  |  | 8 |  |  |
| cPropPointSpring | PropPointSpring | 7 |  | 7 | ok /  / ok |
| cPropRebar | PropRebar | 4 | 7 | 4 | ok / ok / ok |
| cPropSolid | PropSolid |  | 8 |  |  / ok /  |
| cPropTendon | PropTendon | 6 | 6 | 6 | ok / ok / ok |
| cReleaseFrame |  |  | 6 |  |  |
| cSapModel |  | 21 | 21 | 21 |  |
| cScheduler | Scheduler |  | 12 |  |  / ok /  |
| cSchedulerCallBack |  |  | 2 |  |  |
| cSectionCut | SectCut | 17 | 17 | 17 | ok / ok / ok |
| cSelect | SelectObj | 6 | 22 | 6 | ok / ok / ok |
| cSolidElm | SolidElm |  | 13 |  |  / ok /  |
| cSolidObj | SolidObj |  | 45 |  |  / ok /  |
| cSpandrelLabel | SpandrelLabel | 6 |  | 6 | ok /  / ok |
| cStory | Story | 17 |  | 17 | ok /  / ok |
| cTendonObj | TendonObj | 17 | 45 | 17 | ok / ok / ok |
| cTower | Tower | 8 |  | 8 | ok /  / ok |
| cView | View | 2 | 2 | 2 | ok / ok / ok |

- ETABS: 129 interfaces, 1338 methods

- SAP2000: 171 interfaces, 2135 methods

- SAFE: 130 interfaces, 1343 methods
