import logging
from io import BytesIO

from config import config
import mysql.connector
from pydicom.filebase import DicomFileLike
from pydicom.filewriter import dcmwrite

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

def extract_mwl_fields(dataset):
    """Extract key fields from DICOM dataset for indexing"""
    return {
        'AccessionNumber': getattr(dataset, 'AccessionNumber', None),
        'StudyInstanceUID': getattr(dataset, 'StudyInstanceUID', None),
        'PatientID': getattr(dataset, 'PatientID', None),
        'PatientName': str(getattr(dataset, 'PatientName', '')),
        'ScheduledProcedureStepStartDate': None,
        'ScheduledStationAETitle': None
    }

def extract_scheduled_fields(dataset):
    """Extract scheduled procedure step fields"""
    scheduled_date = None
    scheduled_aet = None
    
    # Extract from ScheduledProcedureStepSequence if present
    if hasattr(dataset, 'ScheduledProcedureStepSequence') and dataset.ScheduledProcedureStepSequence:
        sps = dataset.ScheduledProcedureStepSequence[0]
        scheduled_date = getattr(sps, 'ScheduledProcedureStepStartDate', None)
        scheduled_aet = getattr(sps, 'ScheduledStationAETitle', None)
    
    return scheduled_date, scheduled_aet

def insert_mwl_record(json_data, dataset):
    """Insert MWL record using simplified schema"""
    conn = None
    cursor = None
    try:
        conn = get_DB()
        cursor = conn.cursor()
        
        # Extract key fields
        fields = extract_mwl_fields(dataset)
        scheduled_date, scheduled_aet = extract_scheduled_fields(dataset)
        
        # Convert dataset to bytes
        dataset_bytes = dataset_to_bytes(dataset)
        
        sql = """
            INSERT INTO mwl
            (completed, AccessionNumber, StudyInstanceUID, PatientID, PatientName,
             ScheduledProcedureStepStartDate, ScheduledStationAETitle, Dataset)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        values = (
            0,  # not completed
            fields['AccessionNumber'],
            fields['StudyInstanceUID'],
            fields['PatientID'],
            fields['PatientName'],
            scheduled_date,
            scheduled_aet,
            dataset_bytes
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
