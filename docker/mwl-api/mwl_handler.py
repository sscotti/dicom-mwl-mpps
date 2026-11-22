from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import generate_uid
import logging
from datetime import datetime

def create_mwl_from_json(json_data):
    """
    Create a DICOM MWL dataset from JSON data using recursion
    """
    ds = Dataset()
    
    def set_dataset_value(dataset, key, value):
        """Recursively set DICOM dataset values"""
        if isinstance(value, dict):
            # Create a sequence
            seq = []
            seq_item = Dataset()
            for k, v in value.items():
                set_dataset_value(seq_item, k, v)
            seq.append(seq_item)
            setattr(dataset, key, seq)
        elif isinstance(value, list):
            # Handle sequences with multiple items
            seq = []
            for item in value:
                seq_item = Dataset()
                if isinstance(item, dict):
                    for k, v in item.items():
                        set_dataset_value(seq_item, k, v)
                seq.append(seq_item)
            setattr(dataset, key, seq)
        else:
            # Set simple value
            setattr(dataset, key, value)

    # Process each key-value pair in the JSON
    for key, value in json_data.items():
        try:
            set_dataset_value(ds, key, value)
        except Exception as e:
            logging.error(f"Error setting {key}: {str(e)}")
            raise

    # Study Instance UID belongs on the worklist item itself.
    if not getattr(ds, "StudyInstanceUID", None):
        ds.StudyInstanceUID = generate_uid()

    # Scheduled step attributes belong inside the sequence a modality queries.
    sequence = getattr(ds, "ScheduledProcedureStepSequence", None)
    if not sequence:
        ds.ScheduledProcedureStepSequence = [Dataset()]
        sequence = ds.ScheduledProcedureStepSequence
    step = sequence[0]
    if not getattr(step, "ScheduledProcedureStepStartDate", None):
        step.ScheduledProcedureStepStartDate = getattr(
            ds, "ScheduledProcedureStepStartDate", None
        ) or datetime.now().strftime("%Y%m%d")
    if not getattr(step, "ScheduledProcedureStepStartTime", None):
        step.ScheduledProcedureStepStartTime = getattr(
            ds, "ScheduledProcedureStepStartTime", None
        ) or datetime.now().strftime("%H%M%S")
    if not getattr(step, "ScheduledProcedureStepID", None):
        step.ScheduledProcedureStepID = "SPS1"
    if not getattr(step, "Modality", None):
        step.Modality = "OT"

    return ds

def create_mwl_file(json_data, output_path=None):
    """
    Create a DICOM MWL file from JSON data
    """
    try:
        ds = create_mwl_from_json(json_data)
        
        # Add file meta information
        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.31'  # Modality Worklist Information Model
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.ImplementationClassUID = generate_uid()
        file_meta.TransferSyntaxUID = '1.2.840.10008.1.2.1'  # Explicit VR Little Endian
        
        # Create the final dataset
        final_ds = FileDataset(output_path if output_path else "", {}, file_meta=file_meta, preamble=b"\0" * 128)
        final_ds.update(ds)
        
        # Save to file if path provided
        if output_path:
            final_ds.save_as(output_path, enforce_file_format=True)
        
        return final_ds
    except Exception as e:
        logging.error(f"Error creating MWL file: {str(e)}")
        raise

