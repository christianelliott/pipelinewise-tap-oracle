import re
import singer
from singer import  metadata
import decimal
import datetime
import dateutil.parser
import oracledb
import base64

def should_sync_column(metadata, field_name):
    field_metadata = metadata.get(('properties', field_name), {})
    return singer.should_sync_field(field_metadata.get('inclusion'),
                                    field_metadata.get('selected'),
                                    True)


def send_schema_message(stream, bookmark_properties):
    s_md = metadata.to_map(stream.metadata)
    if s_md.get((), {}).get('is-view'):
        key_properties = s_md.get((), {}).get('view-key-properties')
        if not key_properties:
            key_properties = []
    else:
        key_properties = s_md.get((), {}).get('table-key-properties')

    schema_message = singer.SchemaMessage(stream=stream.tap_stream_id,
                                          schema=stream.schema.to_dict(),
                                          key_properties=key_properties,
                                          bookmark_properties=bookmark_properties)
    singer.write_message(schema_message)

# singer.decimal is defined as 100 digits plus a decimal point
# NB: If a number exceeds this length, we should normalize it to attempt to persist properly.
MAX_DECIMAL_DIGITS = 101

def row_to_singer_message(stream, row, version, columns, time_extracted):
    row_to_persist = ()
    for idx, elem in enumerate(row):
        property_type = stream.schema.properties[columns[idx]].type
        property_format = stream.schema.properties[columns[idx]].format
        description = stream.schema.properties[columns[idx]].description
        if elem is None:
            row_to_persist += (elem,)
        elif ('string' in property_type or property_type == 'string') and property_format == 'singer.decimal':
            if len(str(elem)) > MAX_DECIMAL_DIGITS:
                elem = elem.normalize()
            if elem is None:
                row_to_persist += (elem,)
            else:
                row_to_persist += (str(elem),)
        elif 'integer' in property_type or property_type == 'integer':
            integer_representation = int(elem)
            row_to_persist += (integer_representation,)
        elif description == 'blob':
            base64encode = base64.b64encode(elem)
            row_to_persist += (base64encode,)
        elif 'boolean' in property_type or property_type == 'boolean':
            retval = False
            if elem == 1: retval = True 
            else: retval = False 
            row_to_persist += (retval,)
        else:
            row_to_persist += (elem,)

    rec = dict(zip(columns, row_to_persist))

    return singer.RecordMessage(
       stream=stream.tap_stream_id,
       record=rec,
       version=version,
       time_extracted=time_extracted)

def OutputTypeHandler(cursor, name, defaultType, size, precision, scale):
   if defaultType == oracledb.NUMBER:
      return cursor.var(decimal.Decimal, arraysize = cursor.arraysize)
   if defaultType == oracledb.CLOB:
      return cursor.var(oracledb.LONG_STRING, arraysize=cursor.arraysize)
   if defaultType == oracledb.NCLOB:
      return cursor.var(oracledb.LONG_STRING, arraysize=cursor.arraysize)
   if defaultType == oracledb.BLOB:
      return cursor.var(oracledb.LONG_BINARY, arraysize=cursor.arraysize)


def prepare_columns_sql(stream, c):
   column_name = """ "{}" """.format(c)
   if 'string' in stream.schema.properties[c].type and stream.schema.properties[c].format == 'date-time':
      return "to_char({})".format(column_name)
   return column_name

def prepare_where_clause_arg(val, sql_datatype):
    if sql_datatype == 'NUMBER':
        return val
    elif sql_datatype == 'DATE':
        return "to_date('{}')".format(val)
    elif re.search('TIMESTAMP\([0-9]\) WITH (LOCAL )?TIME ZONE', sql_datatype):
        return "to_timestamp_tz('{}')".format(val)
    elif re.search('TIMESTAMP\([0-9]\)', sql_datatype):
        return "to_timestamp('{}')".format(val)
    else:
        return "'{}'".format(val)
