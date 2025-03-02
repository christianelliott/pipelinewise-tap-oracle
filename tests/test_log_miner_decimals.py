import unittest
import os
import oracledb, sys, string, datetime
import tap_oracle
import pdb
import singer
from singer import get_logger, metadata, write_bookmark
from tests.utils import get_test_connection, get_test_conn_config, ensure_test_table, select_all_of_stream, set_replication_method_for_stream, crud_up_log_miner_fixtures, verify_crud_messages
import tap_oracle.sync_strategies.log_miner as log_miner
import tap_oracle.sync_strategies.full_table as full_table
import decimal

LOGGER = get_logger()

CAUGHT_MESSAGES = []
full_table.UPDATE_BOOKMARK_PERIOD = 1000

def singer_write_message(message):
    CAUGHT_MESSAGES.append(message)

def do_not_dump_catalog(catalog):
    pass

class MineDecimals(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        tap_oracle.dump_catalog = do_not_dump_catalog
        full_table.UPDATE_BOOKMARK_PERIOD = 1000

        with get_test_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                begin
                    rdsadmin.rdsadmin_util.set_configuration(
                        name  => 'archivelog retention hours',
                        value => '24');
                end;
            """)

            cur.execute("""
                begin
                   rdsadmin.rdsadmin_util.alter_supplemental_logging(
                      p_action => 'ADD');
                end;
            """)

            result = cur.execute("select log_mode from v$database").fetchall()
            self.assertEqual(result[0][0], "ARCHIVELOG")


        table_spec = {"columns": [{"name" : '"our_number"',                "type" : "number", "primary_key": True, "identity": True},
                                  {"name" : '"our_number_10_2"',           "type" : "number(10,2)"},
                                  {"name" : '"our_number_38_4"',           "type" : "number(38,4)"}],
                     "name" : "CHICKEN"}

        ensure_test_table(table_spec)


    def update_add_5(self, v):
        if v is not None:
            return v + 5
        else:
            return None

    def test_catalog(self):

        singer.write_message = singer_write_message
        log_miner.UPDATE_BOOKMARK_PERIOD = 1

        with get_test_connection() as conn:
            conn.autocommit = True
            catalog = tap_oracle.do_discovery(get_test_conn_config(), [])
            chicken_stream = [s for s in catalog.streams if s.table == 'CHICKEN'][0]
            chicken_stream = select_all_of_stream(chicken_stream)

            chicken_stream = set_replication_method_for_stream(chicken_stream, 'LOG_BASED')

            cur = conn.cursor()
            prev_scn = cur.execute("SELECT current_scn FROM V$DATABASE").fetchall()[0][0]

            crud_up_log_miner_fixtures(cur, 'CHICKEN',
                                       {
                                           '"our_number_10_2"': decimal.Decimal('100.11'),
                                           '"our_number_38_4"': decimal.Decimal('99999999999999999.99991')
                                       }, self.update_add_5)

            post_scn = cur.execute("SELECT current_scn FROM V$DATABASE").fetchall()[0][0]
            LOGGER.info("post SCN: {}".format(post_scn))

            state = write_bookmark({}, chicken_stream.tap_stream_id, 'scn', prev_scn)
            state = write_bookmark(state, chicken_stream.tap_stream_id, 'version', 1)
            tap_oracle.do_sync(get_test_conn_config(), catalog, None, state)

            verify_crud_messages(self, CAUGHT_MESSAGES, ['our_number'])

            #verify message 1 - first insert
            insert_rec_1 = CAUGHT_MESSAGES[1].record
            self.assertIsNotNone(insert_rec_1.get('scn'))
            insert_rec_1.pop('scn')
            self.assertEqual(insert_rec_1, {'our_number_38_4': decimal.Decimal('99999999999999999.9999'), 'our_number': 1, 'our_number_10_2': decimal.Decimal('100.11'), '_sdc_deleted_at': None})


            #verify UPDATE
            update_rec = CAUGHT_MESSAGES[5].record
            self.assertIsNotNone(update_rec.get('scn'))
            update_rec.pop('scn')
            self.assertEqual(update_rec, {'our_number_38_4': decimal.Decimal('100000000000000004.9999'), 'our_number': 1, 'our_number_10_2': decimal.Decimal('105.11'), '_sdc_deleted_at': None})

            #verify first DELETE message
            delete_rec = CAUGHT_MESSAGES[9].record
            self.assertIsNotNone(delete_rec.get('scn'))
            self.assertIsNotNone(delete_rec.get('_sdc_deleted_at'))
            delete_rec.pop('scn')
            delete_rec.pop('_sdc_deleted_at')
            self.assertEqual(delete_rec,
                             {'our_number_38_4': decimal.Decimal('100000000000000004.9999'),
                              'our_number': 1,
                              'our_number_10_2': decimal.Decimal('105.11')})


            #verify second DELETE message
            delete_rec_2 = CAUGHT_MESSAGES[11].record
            self.assertIsNotNone(delete_rec_2.get('scn'))
            self.assertIsNotNone(delete_rec_2.get('_sdc_deleted_at'))
            delete_rec_2.pop('scn')
            delete_rec_2.pop('_sdc_deleted_at')
            self.assertEqual(delete_rec_2,
                             {'our_number_38_4': decimal.Decimal('100000000000000004.9999'),
                              'our_number': 2,
                              'our_number_10_2': decimal.Decimal('105.11')})




if __name__== "__main__":
    test1 = MineDecimals()
    test1.setUp()
    test1.test_catalog()
