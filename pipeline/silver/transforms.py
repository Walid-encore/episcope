"""Column-level building blocks shared by silver (and later gold). All return Spark Columns."""
from __future__ import annotations

from pyspark.sql import Column, functions as F


def blank(col: Column) -> Column:
    return col.isNull() | (F.trim(col) == "")


def parse_date(col: Column) -> Column:
    s = F.trim(col)
    return F.when(s.rlike(r"^\d{4}-\d{2}-\d{2}$"), F.to_date(s, "yyyy-MM-dd"))


def parse_int(col: Column) -> Column:
    s = F.trim(col)
    return F.when(s.rlike(r"^[0-9]{1,12}$"), s.cast("long"))


def parse_double(col: Column) -> Column:
    s = F.trim(col)
    return F.when(s.rlike(r"^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$"), s.cast("double"))


def parse_bool(col: Column) -> Column:
    s = F.lower(F.trim(col))
    return F.when(s == "true", F.lit(True)).when(s == "false", F.lit(False))


def hash_id(col: Column, salt: str) -> Column:
    """Salted SHA-256 of an identifier. Null/blank ids stay null (never hash the bare salt)."""
    return F.when(~blank(col), F.sha2(F.concat_ws("|", F.lit(salt), F.trim(col)), 256))


def age_band(age: Column) -> Column:
    return (F.when(age.isNull(), F.lit(None).cast("string"))
            .when(age <= 4, "0-4").when(age <= 14, "5-14").when(age <= 24, "15-24")
            .when(age <= 44, "25-44").when(age <= 64, "45-64").otherwise("65+"))


def disease_from_icd(code: Column) -> Column:
    p = F.substring(code, 1, 3)
    return (F.when(p.isin("A90", "A91"), "dengue")
            .when(p.isin("A00", "A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"), "diarrhoeal")
            .when(p.isin("J10", "J11"), "ili")
            .when(p.isin("B50", "B51", "B52", "B53", "B54"), "malaria")
            .otherwise("other"))


# Epi-weeks follow ISO 8601: weeks start on Monday; the week belongs to the year of its Thursday.
def _thursday(d: Column) -> Column:
    return F.date_add(F.date_trunc("week", d).cast("date"), 3)


def epi_year(d: Column) -> Column:
    return F.year(_thursday(d))


def epi_week(d: Column) -> Column:
    return F.weekofyear(_thursday(d))


def week_start(d: Column) -> Column:
    return F.date_trunc("week", d).cast("date")
