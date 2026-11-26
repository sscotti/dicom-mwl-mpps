import logging
from io import BytesIO
from typing import Any, Optional

from config import config
import mysql.connector
from pydicom.dataset import Dataset
from pydicom.filebase import DicomFileLike
from pydicom.filereader import dcmread
from pydicom.filewriter import dcmwrite
from pydicom.sequence import Sequence


class DuplicateAccession(Exception):
    """The accession number is already stored."""


def get_DB():
    return mysql.connector.connect(
        host=config.database.host,
        port=config.database.port,
        user=config.database.user,
        password=config.database.password,
        database=config.database.name,
    )


def dataset_to_bytes(dataset):
    """Convert DICOM dataset to bytes"""
    with BytesIO() as buffer:
        memory_dataset = DicomFileLike(buffer)
        dcmwrite(memory_dataset, dataset)
        memory_dataset.seek(0)
        return memory_dataset.read()


def bytes_to_dataset(blob: bytes) -> Dataset:
    """Decode a stored MWL Dataset blob."""
    return dcmread(BytesIO(blob), force=True)


def _element_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Sequence):
        return [dataset_to_jsonable(item) for item in value]
    if isinstance(value, Dataset):
        return dataset_to_jsonable(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return f"<{len(value)} bytes>"
    return str(value)


def dataset_to_jsonable(dataset: Dataset) -> dict[str, Any]:
    """Nested dict of keyword → value (sequences expanded)."""
    out: dict[str, Any] = {}
    for elem in dataset:
        key = elem.keyword or str(elem.tag)
        out[key] = _element_value(elem.value)
    return out


def dataset_to_rows(dataset: Dataset, prefix: str = "") -> list[dict[str, str]]:
    """Flat rows for HTML: tag, keyword, VR, value."""
    rows: list[dict[str, str]] = []
    for elem in dataset:
        tag = f"({elem.tag.group:04X},{elem.tag.element:04X})"
        keyword = elem.keyword or ""
        path = f"{prefix}{keyword}" if keyword else f"{prefix}{tag}"
        if elem.VR == "SQ":
            rows.append(
                {
                    "tag": tag,
                    "keyword": path,
                    "vr": "SQ",
                    "value": f"{len(elem.value)} item(s)",
                }
            )
            for i, item in enumerate(elem.value):
                rows.extend(dataset_to_rows(item, prefix=f"{path}[{i}]."))
        else:
            rows.append(
                {
                    "tag": tag,
                    "keyword": path,
                    "vr": elem.VR,
                    "value": _element_value(elem.value) or "",
                }
            )
    return rows


def get_mwl_row(row_id: int) -> Optional[dict]:
    """Fetch one MWL row including Dataset blob."""
    conn = get_DB()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, AccessionNumber, StudyInstanceUID, PatientID, PatientName,
                   ScheduledProcedureStepStartDate, ScheduledStationAETitle,
                   completed, created_at, Dataset
            FROM mwl
            WHERE id = %s
            """,
            (row_id,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def extract_mwl_fields(dataset):
    """Extract key fields from DICOM dataset for indexing"""
    return {
        "AccessionNumber": getattr(dataset, "AccessionNumber", None),
        "StudyInstanceUID": getattr(dataset, "StudyInstanceUID", None),
        "PatientID": getattr(dataset, "PatientID", None),
        "PatientName": str(getattr(dataset, "PatientName", "")),
        "ScheduledProcedureStepStartDate": None,
        "ScheduledStationAETitle": None,
    }


def extract_scheduled_fields(dataset):
    """Extract scheduled procedure step fields"""
    scheduled_date = None
    scheduled_aet = None

    if hasattr(dataset, "ScheduledProcedureStepSequence") and dataset.ScheduledProcedureStepSequence:
        sps = dataset.ScheduledProcedureStepSequence[0]
        scheduled_date = getattr(sps, "ScheduledProcedureStepStartDate", None)
        scheduled_aet = getattr(sps, "ScheduledStationAETitle", None)

    return scheduled_date, scheduled_aet


def insert_mwl_record(json_data, dataset):
    """Insert MWL record using simplified schema"""
    conn = None
    cursor = None
    fields = {"AccessionNumber": None}
    try:
        conn = get_DB()
        cursor = conn.cursor()

        fields = extract_mwl_fields(dataset)
        scheduled_date, scheduled_aet = extract_scheduled_fields(dataset)
        dataset_bytes = dataset_to_bytes(dataset)

        sql = """
            INSERT INTO mwl
            (completed, AccessionNumber, StudyInstanceUID, PatientID, PatientName,
             ScheduledProcedureStepStartDate, ScheduledStationAETitle, Dataset)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        values = (
            0,
            fields["AccessionNumber"],
            fields["StudyInstanceUID"],
            fields["PatientID"],
            fields["PatientName"],
            scheduled_date,
            scheduled_aet,
            dataset_bytes,
        )

        logging.info("Inserting MWL record for AccessionNumber: %s", fields["AccessionNumber"])
        cursor.execute(sql, values)
        conn.commit()
        row_id = cursor.lastrowid
        logging.info("Inserted MWL record with id %s", row_id)
        return row_id

    except mysql.connector.IntegrityError as exc:
        if conn:
            conn.rollback()
        if getattr(exc, "errno", None) == 1062:
            raise DuplicateAccession(fields["AccessionNumber"]) from exc
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
