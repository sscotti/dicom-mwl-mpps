from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    ModalityWorklistInformationFind,
    ModalityPerformedProcedureStep,
    Verification,
)
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, ExplicitVRBigEndian
import fnmatch
import logging
import re
import time
from contextlib import contextmanager

from config import config
import mysql.connector
from pydicom.dataset import Dataset
from pydicom import dcmread
from io import BytesIO
from pydicom.filebase import DicomFileLike
from pydicom.filewriter import dcmwrite

logging.basicConfig(
    level=getattr(logging, config.logging.level.upper(), logging.INFO),
    format=config.logging.format,
)

def get_DB():
    return mysql.connector.connect(
        host=config.database.host,
        port=config.database.port,
        user=config.database.user,
        password=config.database.password,
        database=config.database.name,
    )

@contextmanager
def db_session(dictionary=False):
    """Open a connection and close it on every exit, including exceptions."""
    conn = get_DB()
    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def dataset_to_bytes(dataset):
    """Convert DICOM dataset to bytes"""
    with BytesIO() as buffer:
        memory_dataset = DicomFileLike(buffer)
        dcmwrite(memory_dataset, dataset)
        memory_dataset.seek(0)
        return memory_dataset.read()

def bytes_to_dataset(blob):
    """Convert DICOM blob to dataset"""
    try:
        dataset = dcmread(BytesIO(blob), force=True)
        return dataset
    except Exception as e:
        logging.error(f"Failed to convert blob to dataset: {e}")
        raise

def mpps_value(dataset, keyword):
    """Read an MPPS attribute from the scheduled-step sequence, then the root dataset.

    Modalities send Accession Number and Study Instance UID inside
    Scheduled Step Attributes Sequence, not as top-level MPPS attributes.
    """
    sequence = getattr(dataset, "ScheduledStepAttributesSequence", None)
    if sequence:
        value = getattr(sequence[0], keyword, None)
        if value not in (None, ""):
            return value
    value = getattr(dataset, keyword, None)
    return value if value not in (None, "") else None

def is_universal(value):
    """An empty query value asks for the attribute back and matches anything."""
    if value is None:
        return True
    if isinstance(value, (str, bytes, bytearray)):
        return len(value) == 0
    try:
        return len(value) == 0
    except TypeError:
        return False

def value_matches(stored, query):
    if is_universal(query):
        return True
    query_text = str(query).strip()
    if query_text == "":
        return True
    stored_text = "" if stored is None else str(stored).strip()
    return any(single_value_matches(stored_text, alternative) for alternative in query_text.split("\\"))

def single_value_matches(stored_text, query_text):
    if re.fullmatch(r"\d{0,8}-\d{0,8}", query_text):
        if not re.fullmatch(r"\d{8}", stored_text):
            return False
        start, end = query_text.split("-", 1)
        if start and stored_text < start:
            return False
        if end and stored_text > end:
            return False
        return True
    if "*" in query_text or "?" in query_text:
        return fnmatch.fnmatch(stored_text.upper(), query_text.upper())
    return stored_text.upper() == query_text.upper()

def dataset_matches(candidate, query):
    if candidate is None:
        return False
    for elem in query:
        if elem.tag.group in (0x0000, 0x0002):
            continue
        stored_elem = candidate.get(elem.tag)
        stored = None if stored_elem is None else stored_elem.value
        if elem.VR == "SQ":
            if is_universal(elem.value):
                continue
            if not sequence_matches(stored, elem.value):
                logging.debug("Query mismatch on sequence %s", elem.keyword or elem.tag)
                return False
            continue
        if not value_matches(stored, elem.value):
            logging.debug("Query mismatch on %s: query=%r stored=%r", elem.keyword or elem.tag, elem.value, stored)
            return False
    return True

def sequence_matches(stored_sequence, query_sequence):
    if not stored_sequence:
        return False
    for query_item in query_sequence:
        if not any(dataset_matches(stored_item, query_item) for stored_item in stored_sequence):
            return False
    return True

def matches_query(mwl_ds, query_ds):
    """Match an MWL dataset against a C-FIND identifier.

    Empty values are universal matches. Sequences match item by item.
    ``*`` and ``?`` are wildcards, and a DICOM date range uses ``-``.
    """
    return dataset_matches(mwl_ds, query_ds)

def handle_mwl(event):
    """Handle C-FIND requests for Modality Worklist"""
    logging.info("Received MWL C-FIND request")
    query_ds = event.identifier

    try:
        with db_session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT Dataset FROM mwl WHERE completed = 0")
            rows = cursor.fetchall()
    except Exception:
        logging.exception("MWL DB query failed")
        yield 0xC000, None
        return

    try:
        match_count = 0
        for row in rows:
            mwl_ds = bytes_to_dataset(row["Dataset"])
            logging.info("Processing dataset with AccessionNumber: %s", getattr(mwl_ds, "AccessionNumber", "N/A"))
            if matches_query(mwl_ds, query_ds):
                yield 0xFF00, mwl_ds
                match_count += 1
        logging.info("C-FIND finished with %s match(es)", match_count)
    except Exception:
        logging.exception("MWL C-FIND failed while matching")
        yield 0xC000, None

def handle_n_create(event):
    """Handle MPPS N-CREATE requests (procedure start)"""
    logging.info("Received MPPS N-CREATE")

    sop_instance_uid = event.request.AffectedSOPInstanceUID
    dataset = event.attribute_list
    if dataset is None:
        logging.error("N-CREATE has no attribute list")
        return 0xC000, None

    logging.info("N-CREATE for SOP Instance UID: %s", sop_instance_uid)

    accession_number = mpps_value(dataset, "AccessionNumber")
    study_instance_uid = mpps_value(dataset, "StudyInstanceUID")
    patient_id = getattr(dataset, "PatientID", None)

    raw_status = getattr(dataset, "PerformedProcedureStepStatus", "IN PROGRESS")
    status_mapping = {
        "IN PROGRESS": "IN_PROGRESS",
        "COMPLETED": "COMPLETED",
        "DISCONTINUED": "DISCONTINUED",
    }
    pps_status = status_mapping.get(raw_status, "IN_PROGRESS")
    pps_id = getattr(dataset, "PerformedProcedureStepID", None)
    performed_station_ae = getattr(dataset, "PerformedStationAETitle", None)
    logging.info("AccessionNumber: %s StudyUID: %s status: %s", accession_number, study_instance_uid, pps_status)

    try:
        with db_session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM mpps WHERE sop_instance_uid = %s",
                (sop_instance_uid,),
            )
            if cursor.fetchone():
                logging.error("Duplicate SOP Instance UID: %s", sop_instance_uid)
                return 0x0111, None

            # An unscheduled exam has no worklist row. Store the MPPS and say so.
            mwl_id = None
            if accession_number:
                cursor.execute(
                    "SELECT id FROM mwl WHERE AccessionNumber = %s AND completed = 0",
                    (accession_number,),
                )
                mwl_row = cursor.fetchone()
                if mwl_row:
                    mwl_id = mwl_row["id"]
                    logging.info("Found related MWL with ID: %s", mwl_id)
                else:
                    logging.warning(
                        "Accession %s has no open worklist item; storing MPPS without a link",
                        accession_number,
                    )

            cursor.execute(
                """
                INSERT INTO mpps
                (sop_instance_uid, mwl_id, AccessionNumber, StudyInstanceUID, PatientID,
                 status, performed_procedure_step_id, performed_station_ae_title, dataset_blob)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sop_instance_uid,
                    mwl_id,
                    accession_number,
                    study_instance_uid,
                    patient_id,
                    pps_status,
                    pps_id,
                    performed_station_ae,
                    dataset_to_bytes(dataset),
                ),
            )
            record_id = cursor.lastrowid
    except Exception:
        logging.exception("Error handling N-CREATE")
        return 0xC000, None

    logging.info("Created MPPS record with ID: %s", record_id)
    return 0x0000, dataset

# Also update handle_n_set with similar status mapping
def handle_n_set(event):
    """Handle MPPS N-SET requests (procedure update/completion)"""
    logging.info("Received MPPS N-SET")

    sop_instance_uid = event.request.RequestedSOPInstanceUID
    modification_list = event.modification_list

    if sop_instance_uid is None and modification_list:
        sop_instance_uid = getattr(modification_list, "SOPInstanceUID", None)

    logging.info("N-SET for SOP Instance UID: %s", sop_instance_uid)

    if sop_instance_uid is None:
        logging.error("No SOP Instance UID found in N-SET request")
        return 0xC000, None

    raw_status = getattr(modification_list, "PerformedProcedureStepStatus", None)
    status_mapping = {
        "IN PROGRESS": "IN_PROGRESS",
        "COMPLETED": "COMPLETED",
        "DISCONTINUED": "DISCONTINUED",
    }
    procedure_step_status = status_mapping.get(raw_status, raw_status) if raw_status else None
    logging.info("Mapped status: %s", procedure_step_status)

    try:
        with db_session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT dataset_blob, AccessionNumber FROM mpps WHERE sop_instance_uid = %s",
                (sop_instance_uid,),
            )
            existing = cursor.fetchone()
            if not existing:
                logging.error("No MPPS record found with SOP Instance UID: %s", sop_instance_uid)
                return 0xC001, None

            stored = bytes_to_dataset(existing["dataset_blob"]) if existing["dataset_blob"] else Dataset()
            if modification_list is not None:
                stored.update(modification_list)
            dataset_bytes = dataset_to_bytes(stored)

            if procedure_step_status in ("COMPLETED", "DISCONTINUED"):
                cursor.execute(
                    """
                    UPDATE mpps
                    SET status = %s, completed_at = NOW(), dataset_blob = %s
                    WHERE sop_instance_uid = %s
                    """,
                    (procedure_step_status, dataset_bytes, sop_instance_uid),
                )
            elif procedure_step_status:
                cursor.execute(
                    """
                    UPDATE mpps
                    SET status = %s, dataset_blob = %s
                    WHERE sop_instance_uid = %s
                    """,
                    (procedure_step_status, dataset_bytes, sop_instance_uid),
                )
            else:
                cursor.execute(
                    "UPDATE mpps SET dataset_blob = %s WHERE sop_instance_uid = %s",
                    (dataset_bytes, sop_instance_uid),
                )
            rows_affected = cursor.rowcount

            if procedure_step_status in ("COMPLETED", "DISCONTINUED") and rows_affected > 0 and existing["AccessionNumber"]:
                cursor.execute(
                    "UPDATE mwl SET completed = 1 WHERE AccessionNumber = %s",
                    (existing["AccessionNumber"],),
                )
                logging.info("Marked MWL completed for AccessionNumber: %s", existing["AccessionNumber"])
    except Exception:
        logging.exception("Error handling N-SET")
        return 0xC000, None

    if rows_affected > 0:
        logging.info("Updated MPPS status to: %s", procedure_step_status)
        return 0x0000, modification_list
    logging.error("No MPPS record found with SOP Instance UID: %s", sop_instance_uid)
    return 0xC001, None

handlers = [
    (evt.EVT_C_FIND, handle_mwl),
    (evt.EVT_N_CREATE, handle_n_create),
    (evt.EVT_N_SET, handle_n_set),
]

def wait_for_database(attempts=30, delay=2):
    """Block until MySQL accepts a connection."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            conn = get_DB()
            conn.close()
            logging.info("Database connection ready")
            return
        except Exception as exc:
            last_error = exc
            logging.warning("Waiting for database (%s/%s): %s", attempt, attempts, exc)
            time.sleep(delay)
    raise SystemExit(f"Database did not become ready: {last_error}")

# Repo default from config. pynetdicom would otherwise announce PYNETDICOM.
ae = AE()
ae.ae_title = config.dicom.ae_title
ae.maximum_associations = config.dicom.max_associations
_ts = [ExplicitVRLittleEndian, ImplicitVRLittleEndian, ExplicitVRBigEndian]
# C-ECHO so Horos/IsiX "Verify" succeeds (MWL-only SCP otherwise rejects Verification).
ae.add_supported_context(Verification, _ts)
ae.add_supported_context(ModalityWorklistInformationFind, _ts)
ae.add_supported_context(ModalityPerformedProcedureStep, _ts)

wait_for_database()
ae.start_server((config.dicom.bind_address, config.dicom.port), evt_handlers=handlers, block=True)