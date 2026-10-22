"""Data-quality rule catalogue (DQ-1). Every rule has a code (the quarantine reason code).

severity "error": the row goes to quarantine.   severity "warn": the row is kept (and repaired
where noted), and the failure is only counted in the DQ report.

Rules are built inside functions because Spark Columns need an active Spark session.
Constants below come from docs/schema.md (the contract), not from the data.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import Column, functions as F

from pipeline.silver import transforms as T

MIN_ONSET_DATE = "2021-01-01"      # schema.md: onset_date not before 2021-01-01
MAX_DELAY_DAYS = 21                # schema.md: report_date = onset_date + 0..21 days
MAX_AGE = 120                      # schema.md: valid ages 0..120
VALID_SEX = ("M", "F")
VALID_OUTCOMES = ("recovered", "died", "referred", "ongoing")
FACILITY_TYPES = ("hospital", "upazila_health_complex", "clinic")
ICD_PATTERN = r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$"


@dataclass
class Rule:
    code: str
    description: str
    fail: Column
    severity: str = "error"


def rule(code: str, description: str, expr: Column, severity: str = "error") -> Rule:
    return Rule(code, description, F.coalesce(expr, F.lit(False)), severity)


c = F.col


def encounter_rules() -> list[Rule]:
    return [
        rule("ENC_ID_MISSING", "encounter_id is empty", T.blank(c("encounter_id"))),
        rule("PATIENT_ID_MISSING", "patient_id is empty", T.blank(c("patient_id"))),
        rule("FACILITY_ID_MISSING", "facility_id is empty", T.blank(c("facility_id"))),
        rule("FACILITY_ORPHAN", "facility_id not in the facility registry (referential integrity)",
             ~T.blank(c("facility_id")) & c("fac_district_code").isNull()),
        rule("ONSET_DATE_INVALID", "onset_date is not a valid yyyy-MM-dd date", c("onset_d").isNull()),
        rule("REPORT_DATE_INVALID", "report_date is not a valid yyyy-MM-dd date", c("report_d").isNull()),
        rule("ONSET_BEFORE_MIN_DATE", f"onset_date before {MIN_ONSET_DATE}",
             c("onset_d") < F.lit(MIN_ONSET_DATE).cast("date")),
        rule("REPORT_BEFORE_ONSET", "report_date earlier than onset_date", c("report_d") < c("onset_d")),
        rule("REPORT_DELAY_TOO_LONG", f"reporting delay above {MAX_DELAY_DAYS} days",
             c("delay_days") > MAX_DELAY_DAYS),
        rule("REPORT_DATE_IN_FUTURE", "report_date after the date the batch was loaded",
             c("report_d") > c("load_date")),
        rule("AGE_INVALID", f"age_years missing, non-numeric or above {MAX_AGE}",
             c("age_i").isNull() | (c("age_i") > MAX_AGE)),
        rule("ICD10_INVALID", "icd10_code missing or not matching the ICD-10 pattern",
             c("icd_u").isNull() | ~c("icd_u").rlike(ICD_PATTERN)),
        rule("OUTCOME_INVALID", "outcome not one of recovered/died/referred/ongoing",
             c("outcome_l").isNull() | ~c("outcome_l").isin(*VALID_OUTCOMES)),
        rule("LAB_FLAG_INVALID", "lab_confirmed is not true/false", c("lab_b").isNull()),
        rule("ADMITTED_FLAG_INVALID", "admitted is not true/false", c("adm_b").isNull()),
        rule("SEX_INVALID", "sex not M/F (row kept, sex set to U)",
             c("sex_u").isNull() | ~c("sex_u").isin(*VALID_SEX), severity="warn"),
    ]


def stock_rules() -> list[Rule]:
    counts = ["iv_saline_units_i", "ns1_kits_i", "ors_sachets_i", "antimalarial_courses_i", "occupied_beds_i"]
    any_bad = F.lit(False)
    for col in counts:
        any_bad = any_bad | c(col).isNull()
    return [
        rule("STOCK_DATE_INVALID", "snapshot_date is not a valid date", c("snap_d").isNull()),
        rule("STOCK_DATE_IN_FUTURE", "snapshot_date after the load date", c("snap_d") > c("load_date")),
        rule("STOCK_FACILITY_MISSING", "facility_id is empty", T.blank(c("facility_id"))),
        rule("STOCK_FACILITY_ORPHAN", "facility_id not in the facility registry",
             ~T.blank(c("facility_id")) & c("fac_district_code").isNull()),
        rule("STOCK_COUNT_INVALID", "a stock/bed count is missing, negative or not an integer", any_bad),
        rule("STOCK_OCCUPIED_EXCEEDS_BEDS", "occupied_beds above registry beds (row kept)",
             c("occupied_beds_i") > c("fac_beds"), severity="warn"),
    ]


def facility_rules() -> list[Rule]:
    return [
        rule("FAC_ID_MISSING", "facility_id is empty", T.blank(c("facility_id"))),
        rule("FAC_DISTRICT_UNKNOWN", "district_code not in the district reference table",
             c("ref_district").isNull()),
        rule("FAC_TYPE_INVALID", "facility_type not hospital/upazila_health_complex/clinic",
             c("ftype").isNull() | ~c("ftype").isin(*FACILITY_TYPES)),
        rule("FAC_BEDS_INVALID", "beds missing or negative", c("beds_i").isNull()),
        rule("FAC_ICU_INVALID", "icu_beds missing, negative or above beds",
             c("icu_i").isNull() | (c("icu_i") > c("beds_i"))),
        rule("FAC_COORD_INVALID", "latitude/longitude missing or out of range",
             c("lat_d").isNull() | c("lon_d").isNull() | ~c("lat_d").between(-90, 90) | ~c("lon_d").between(-180, 180)),
    ]


def district_rules() -> list[Rule]:
    return [
        rule("DIST_CODE_MISSING", "district_code is empty", T.blank(c("district_code"))),
        rule("DIST_NAME_MISSING", "district_name is empty", T.blank(c("district_name"))),
        rule("DIST_POPULATION_INVALID", "population missing or not positive",
             c("pop_i").isNull() | (c("pop_i") <= 0)),
        rule("DIST_COORD_INVALID", "centroid missing or out of range",
             c("lat_d").isNull() | c("lon_d").isNull() | ~c("lat_d").between(-90, 90) | ~c("lon_d").between(-180, 180)),
    ]


def weather_rules() -> list[Rule]:
    return [
        rule("WX_DISTRICT_UNKNOWN", "district_code not in the district reference table", c("ref_district").isNull()),
        rule("WX_DATE_INVALID", "date is not a valid date", c("wx_d").isNull()),
        rule("WX_PRECIP_INVALID", "precip_mm missing or negative", c("precip_d").isNull() | (c("precip_d") < 0)),
        rule("WX_TEMP_INVALID", "temp_mean_c missing or outside -20..55",
             c("temp_d").isNull() | ~c("temp_d").between(-20, 55)),
        rule("WX_HUMIDITY_INVALID", "humidity_pct missing or outside 0..100",
             c("hum_d").isNull() | ~c("hum_d").between(0, 100)),
    ]
