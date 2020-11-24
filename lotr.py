# pylint: disable=C0302
""" Helper functions for LotR ALeP workflow.
"""
import hashlib
import logging
import math
import os
import re
import shutil
import subprocess
import time
import zipfile

import xml.etree.ElementTree as ET
import png
import py7zr
import requests
import xlwings as xw
import yaml

from reportlab.lib.pagesizes import landscape, letter, A4
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas


SET_FIRST_ROW = 3
SET_MAX_ROW = 102
SET_GUID_COLUMN = 'A'
SET_NAME_COLUMN = 'B'
SET_VERSION_COLUMN = 'C'
SET_COPYRIGHT_COLUMN = 'D'
SET_LANGUAGE_COLUMN = 'E'

CARD_FIRST_ROW = 2
CARD_MAX_ROW = 10001
CARD_SET_COLUMN = 'A'
CARD_GUID_COLUMN = 'B'
CARD_NUMBER_COLUMN = 'C'
CARD_MAX_COLUMN = 'AZ'

# Name, Traits:Keywords, Text:Flavour, Side B,
# Traits:Keywords, Text:Flavour, Adventure
TRANSLATION_RANGES = ['F{}:F{}', 'J{}:K{}', 'T{}:W{}', 'AB{}:AB{}',
                      'AF{}:AG{}', 'AP{}:AS{}', 'AZ{}:AZ{}']

GIMP_COMMAND = '"{}" -i -b "({} 1 \\"{}\\" \\"{}\\")" -b "(gimp-quit 0)"'
IMAGES_CUSTOM_FOLDER = 'custom'
MAGICK_COMMAND = '"{}" mogrify -profile USWebCoatedSWOP.icc "{}\\*.jpg"'
OCTGN_ARCHIVE = 'unzip-me-into-sets-folder.zip'
PROCESSED_ARTWORK_FOLDER = 'processed'
PROJECT_FOLDER = 'Frogmorton'
SHEET_NAME = 'setExcel'
TEXT_CHUNK_FLAG = b'tEXt'

CONFIGURATION_PATH = 'configuration.yaml'
IMAGES_BACK_PATH = 'imagesBack'
IMAGES_CUSTOM_PATH = os.path.join(PROJECT_FOLDER, 'imagesCustom')
IMAGES_EONS_PATH = 'imagesEons'
IMAGES_RAW_PATH = os.path.join(PROJECT_FOLDER, 'imagesRaw')
IMAGES_ZIP_PATH = '{}/Export/'.format(os.path.split(PROJECT_FOLDER)[-1])
MACROS_PATH = 'macros.xlsm'
MACROS_COPY_PATH = 'macros_copy.xlsm'
OCTGN_ZIP_PATH = 'a21af4e8-be4b-4cda-a6b6-534f9717391f/Sets'
OUTPUT_DB_PATH = os.path.join('Output', 'DB')
OUTPUT_DTC_PATH = os.path.join('Output', 'DriveThruCards')
OUTPUT_MPC_PATH = os.path.join('Output', 'MakePlayingCards')
OUTPUT_OCTGN_PATH = os.path.join('Output', 'OCTGN')
OUTPUT_PDF_PATH = os.path.join('Output', 'PDF')
PROJECT_PATH = 'setGenerator.seproject'
SET_EONS_PATH = 'setEons'
SET_OCTGN_PATH = 'setOCTGN'
SHEET_ROOT_PATH = ''
TEMP_ROOT_PATH = 'Temp'
XML_PATH = os.path.join(PROJECT_FOLDER, 'XML')

ARTWORK_CACHE = {}


def _c2n(column):
    """ Convert column to number.
    """
    res = 0
    multiplier = 1
    column = column.upper()
    for symbol in column[::-1]:
        res += (ord(symbol) - 64) * multiplier
        multiplier *= 26

    return res


def _clear_folder(folder):
    """ Clear the folder.
    """
    if not os.path.exists(folder):
        return

    for _, _, filenames in os.walk(folder):
        for filename in filenames:
            if filename not in ('seproject', '.gitignore'):
                os.remove(os.path.join(folder, filename))

        break


def _create_folder(folder):
    """ Create the folder if needed.
    """
    if not os.path.exists(folder):
        os.mkdir(folder)


def _delete_folder(folder):
    """ Delete the folder.
    """
    if os.path.exists(folder):
        shutil.rmtree(folder, ignore_errors=True)


def _get_artwork_path(conf, set_id):
    """ Get path to the artwork folder.
    """
    artwork_path = os.path.join(conf['artwork_path'], set_id)
    if not os.path.exists(artwork_path):
        artwork_path = conf['artwork_path']

    return artwork_path


def _find_properties(parent, name):
    """ Find properties with a given name.
    """
    properties = [p for p in parent if p.attrib.get('name') == name]
    return properties


def _clear_modified_images(folder, skip_ids):
    """ Delete images for outdated or modified cards inside the folder.
    """
    for _, _, filenames in os.walk(folder):
        for filename in filenames:
            if filename.split('.')[-1] in ('jpg', 'png'):
                card_id = filename[50:86]
                if card_id not in skip_ids:
                    os.remove(os.path.join(folder, filename))

        break


def _update_zip_filename(filename):
    """ Update filename found in the Strange Eons project archive.
    """
    output_filename = os.path.split(filename)[-1]
    output_filename = output_filename.encode('ascii', errors='replace'
                                             ).decode().replace('?', ' ')
    parts = output_filename.split('.')
    output_filename = '.'.join(parts[:-3] + [parts[-1]])
    output_filename = re.sub(r'-2-1(?=\.(?:jpg|png)$)', '-2', output_filename)
    return output_filename


def read_conf():
    """ Read project configuration.
    """
    logging.info('Reading project configuration...')
    timestamp = time.time()

    with open(CONFIGURATION_PATH, 'r') as f_conf:
        conf = yaml.safe_load(f_conf)

    conf['outputs'] = set(conf['outputs'])

    if ('makeplayingcards_zip' in conf['outputs']
            or 'makeplayingcards_7z' in conf['outputs']):
        conf['outputs'].add('makeplayingcards')

    if ('drivethrucards_zip' in conf['outputs']
            or 'drivethrucards_7z' in conf['outputs']):
        conf['outputs'].add('drivethrucards')

    logging.info('...Reading project configuration (%ss)',
                 round(time.time() - timestamp, 3))
    return conf


def clear_project_folders():
    """ Clear raw image files and xml files in the project folders.
    """
    logging.info('Clearing the project folders...')
    timestamp = time.time()

    _clear_folder(IMAGES_CUSTOM_PATH)
    _clear_folder(IMAGES_RAW_PATH)
    _clear_folder(XML_PATH)
    logging.info('...Clearing the project folders (%ss)',
                 round(time.time() - timestamp, 3))


def download_sheet(conf):
    """ Download cards spreadsheet from Google Drive.
    """
    logging.info('Downloading cards spreadsheet from Google Drive...')
    timestamp = time.time()

    if conf['sheet_gdid']:
        sheet_path = os.path.join(SHEET_ROOT_PATH,
                                  '{}.{}'.format(SHEET_NAME,
                                                 conf['sheet_type']))
        if conf['sheet_type'] == 'xlsm':
            url = (
                'https://drive.google.com/uc?export=download&id={}'
                .format(conf['sheet_gdid']))
        else:
            url = (
                'https://docs.google.com/spreadsheets/d/{}/export?format=xlsx'
                .format(conf['sheet_gdid']))

        with open(sheet_path, 'wb') as f_sheet:
            f_sheet.write(requests.get(url).content)
    else:
        logging.info('No Google Drive ID found, using a local copy')

    logging.info('...Downloading cards spreadsheet from Google Drive (%ss)',
                 round(time.time() - timestamp, 3))


def get_sets(conf):
    """ Get all sets to work on and return (id, name, row) tuples.
    """
    logging.info('Getting all sets to work on...')
    timestamp = time.time()

    sheet_path = os.path.join(SHEET_ROOT_PATH,
                              '{}.{}'.format(SHEET_NAME, conf['sheet_type']))

    excel_app = xw.App(visible=False, add_book=False)
    try:
        xlwb = excel_app.books.open(sheet_path)
        try:
            sets = []
            sheet = xlwb.sheets['Sets']
            for row in range(SET_FIRST_ROW, SET_MAX_ROW + 1):
                set_id = sheet.range((row, _c2n(SET_GUID_COLUMN))).value
                if set_id and set_id in conf['set_ids']:
                    sets.append((set_id,
                                 sheet.range((row,
                                              _c2n(SET_NAME_COLUMN))).value,
                                 row))
        finally:
            xlwb.close()
    finally:
        excel_app.quit()

    if not sets:
        logging.error('ERROR: No sets found')

    logging.info('...Getting all sets to work on (%ss)',
                 round(time.time() - timestamp, 3))
    return sets


def _backup_previous_octgn_xml(set_id):
    """ Backup a previous OCTGN xml file.
    """
    new_path = os.path.join(SET_OCTGN_PATH, '{}.xml'.format(set_id))
    old_path = os.path.join(SET_OCTGN_PATH, '{}.xml.old'.format(set_id))
    if os.path.exists(new_path):
        shutil.move(new_path, old_path)


def _copy_octgn_xml(set_id, set_name):
    """ Copy set.xml file to OCTGN output folder.
    """
    output_path = os.path.join(OUTPUT_OCTGN_PATH, set_name)
    _create_folder(output_path)
    output_path = os.path.join(output_path, set_id)
    _create_folder(output_path)
    shutil.copyfile(os.path.join(SET_OCTGN_PATH, '{}.xml'.format(set_id)),
                    os.path.join(output_path, 'set.xml'))


def _backup_previous_xml(conf, set_id, lang):
    """ Backup a previous Strange Eons xml file.
    """
    new_path = os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id, lang))
    old_path = os.path.join(SET_EONS_PATH, '{}.{}.xml.old'.format(set_id,
                                                                  lang))
    if os.path.exists(new_path):
        shutil.move(new_path, old_path)

    if conf['from_scratch'] and os.path.exists(old_path):
        os.remove(old_path)


def _run_macro(conf, set_row, callback):
    """ Prepare a context to run an Excel macro and execute the callback.
    """
    shutil.copyfile(MACROS_PATH, MACROS_COPY_PATH)
    sheet_path = os.path.join(SHEET_ROOT_PATH,
                              '{}.{}'.format(SHEET_NAME, conf['sheet_type']))

    excel_app = xw.App(visible=False, add_book=False)
    try:
        xlwb_source = excel_app.books.open(sheet_path)
        try:
            xlwb_target = excel_app.books.open(MACROS_COPY_PATH)
            try:
                data = xlwb_source.sheets['Sets'].range(
                    '{}{}:{}{}'.format(SET_GUID_COLUMN, set_row,  # pylint: disable=W1308
                                       SET_COPYRIGHT_COLUMN, set_row)).value
                xlwb_target.sheets['Sets'].range(
                    '{}{}:{}{}'.format(SET_GUID_COLUMN, SET_FIRST_ROW,  # pylint: disable=W1308
                                       SET_COPYRIGHT_COLUMN, SET_FIRST_ROW)
                    ).value = data

                card_sheet = xlwb_target.sheets['Card Data']
                card_range = '{}{}:{}{}'.format(CARD_SET_COLUMN,
                                                CARD_FIRST_ROW,
                                                CARD_MAX_COLUMN,
                                                CARD_MAX_ROW)
                data = xlwb_source.sheets['Card Data'].range(card_range).value
                card_sheet.range(card_range).value = data
                card_sheet.range(card_range).api.Sort(
                    Key1=card_sheet.range(
                        '{}:{}'.format(CARD_SET_COLUMN, CARD_SET_COLUMN)  # pylint: disable=W1308
                    ).api,
                    Order1=xw.constants.SortOrder.xlAscending,
                    Key2=card_sheet.range(
                        '{}:{}'.format(CARD_NUMBER_COLUMN, CARD_NUMBER_COLUMN)  # pylint: disable=W1308
                    ).api,
                    Order2=xw.constants.SortOrder.xlAscending)

                callback(xlwb_source, xlwb_target)
                xlwb_target.save()
            finally:
                xlwb_target.close()
        finally:
            xlwb_source.close()
    finally:
        excel_app.quit()


def generate_octgn_xml(conf, set_id, set_name, set_row):
    """ Generate set.xml file for OCTGN.
    """
    def _callback(_, xlwb_target):
        xlwb_target.macro('SaveOCTGN')()

    logging.info('[%s] Generating set.xml file for OCTGN...', set_name)
    timestamp = time.time()

    _backup_previous_octgn_xml(set_id)
    _run_macro(conf, set_row, _callback)
    _copy_octgn_xml(set_id, set_name)
    logging.info('[%s] ...Generating set.xml file for OCTGN (%ss)',
                 set_name, round(time.time() - timestamp, 3))


def generate_xml(conf, set_id, set_name, set_row, lang):
    """ Generate xml file for Strange Eons.
    """
    def _callback(xlwb_source, xlwb_target):
        if lang != 'English':
            translated = []
            tr_sheet = xlwb_source.sheets[lang]
            for source_row in range(CARD_FIRST_ROW, CARD_MAX_ROW + 1):
                if tr_sheet.range((source_row,
                                   _c2n(CARD_SET_COLUMN))).value == set_id:
                    card_id = tr_sheet.range((source_row,
                                              _c2n(CARD_GUID_COLUMN))).value
                    if card_id:
                        translated.append((card_id, source_row))

            api = xlwb_target.sheets['Card Data'].api
            card_sheet = xlwb_target.sheets['Card Data']
            for card_id, source_row in translated:
                cell = api.UsedRange.Find(card_id)
                if cell:
                    target_row = cell.row
                    for tr_range in TRANSLATION_RANGES:
                        source_range = tr_range.format(source_row, source_row)
                        target_range = tr_range.format(target_row, target_row)
                        data = tr_sheet.range(source_range).value
                        card_sheet.range(target_range).value = data

        xlwb_target.sheets['Sets'].range((SET_FIRST_ROW,
                                          _c2n(SET_LANGUAGE_COLUMN))
                                         ).value = lang
        xlwb_target.macro('SaveXML')()

    logging.info('[%s, %s] Generating xml file for Strange Eons...',
                 set_name, lang)
    timestamp = time.time()

    _backup_previous_xml(conf, set_id, lang)
    _run_macro(conf, set_row, _callback)
    logging.info('[%s, %s] ...Generating xml file for Strange Eons (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def _collect_artwork_images(artwork_path):
    """ Collect artwork filenames.
    """
    if artwork_path in ARTWORK_CACHE:
        return ARTWORK_CACHE[artwork_path]

    images = {}
    for _, _, filenames in os.walk(artwork_path):
        for filename in filenames:
            if len(filename.split('.')) < 2 or len(filename.split('_')) < 3:
                continue

            if filename.split('.')[-1] in ('jpg', 'png'):
                card_id_side = '_'.join(filename.split('_')[:2])
                if card_id_side in images:
                    logging.warning('WARNING: Duplicate card ID detected: %s',
                                    os.path.join(artwork_path, filename))

                images[card_id_side] = os.path.join(artwork_path, filename)

        break

    ARTWORK_CACHE[artwork_path] = images
    return images


def _set_outputs(conf, root):
    """ Set required outputs for Strange Eons.
    """
    if ('db' in conf['outputs'] or 'octgn' in conf['outputs']
            or 'pdf' in conf['outputs']):
        root.set('png300NoBleed', '1')

    if 'pdf' in conf['outputs'] or 'drivethrucards' in conf['outputs']:
        root.set('png300Bleed', '1')

    if 'makeplayingcards' in conf['outputs']:
        root.set('png800Bleed', '1')


def _get_property(parent, name):
    """ Get new or existing property with a given name.
    """
    properties = _find_properties(parent, name)
    if properties:
        prop = properties[0]
    else:
        prop = ET.SubElement(parent, 'property')
        prop.set('name', name)

    return prop


def update_xml(conf, set_id, set_name, lang):  # pylint: disable=R0914,R0915
    """ Update the Strange Eons xml file with additional data.
    """
    logging.info('[%s, %s] Updating the Strange Eons xml file with additional'
                 ' data...', set_name, lang)
    timestamp = time.time()

    artwork_path = _get_artwork_path(conf, set_id)
    images = _collect_artwork_images(artwork_path)
    processed_images = _collect_artwork_images(
        os.path.join(artwork_path, PROCESSED_ARTWORK_FOLDER))
    images = {**images, **processed_images}
    xml_path = os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id, lang))

    tree = ET.parse(xml_path)
    root = tree.getroot()
    root.set('pluginVersion', conf['strange_eons_plugin_version'])
    _set_outputs(conf, root)
    encounter_sets = {}
    encounter_cards = {}

    for card in root[0]:
        card_type = _find_properties(card, 'Type')
        if not card_type:
            logging.error('[%s, %s] ERROR: Skipping a card without card type',
                          set_name, lang)
            continue

        card_type = card_type[0].attrib['value']
        encounter_set = _find_properties(card, 'Encounter Set')
        if card_type != 'Quest' and encounter_set:
            encounter_set = encounter_set[0].attrib['value']
            encounter_cards[card.attrib['id']] = encounter_set
            prop = _get_property(card, 'Encounter Set Number')
            prop.set('value', str(encounter_sets.get(encounter_set, 0) + 1))
            quantity = int(
                _find_properties(card, 'Quantity')[0].attrib['value'])
            encounter_sets[encounter_set] = (
                encounter_sets.get(encounter_set, 0) + quantity)

        filename = images.get('{}_{}'.format(card.attrib['id'], 'A'))
        if filename:
            prop = _get_property(card, 'Artwork')
            prop.set('value', os.path.split(filename)[-1])
            prop = _get_property(card, 'Artwork Size')
            prop.set('value', str(os.path.getsize(filename)))
            prop = _get_property(card, 'Artwork Modified')
            prop.set('value', str(int(os.path.getmtime(filename))))

            artist = _find_properties(card, 'Artist')
            if not artist and '_Artist_' in os.path.split(filename)[-1]:
                prop = _get_property(card, 'Artist')
                prop.set('value', '.'.join(
                    '_Artist_'.join(
                        os.path.split(filename)[-1].split('_Artist_')[1:]
                        ).split('.')[:-1]).replace('_', ' '))

        filename = images.get('{}_{}'.format(card.attrib['id'], 'B'))
        alternate = [a for a in card if a.attrib.get('type') == 'B']
        if filename and alternate:
            alternate = alternate[0]
            prop = _get_property(alternate, 'Artwork')
            prop.set('value', os.path.split(filename)[-1])
            prop = _get_property(alternate, 'Artwork Size')
            prop.set('value', str(os.path.getsize(filename)))
            prop = _get_property(alternate, 'Artwork Modified')
            prop.set('value', str(int(os.path.getmtime(filename))))

            artist = _find_properties(alternate, 'Artist')
            if not artist and '_Artist_' in os.path.split(filename)[-1]:
                prop = _get_property(alternate, 'Artist')
                prop.set('value', '.'.join(
                    '_Artist_'.join(
                        os.path.split(filename)[-1].split('_Artist_')[1:]
                        ).split('.')[:-1]).replace('_', ' '))

    for card in root[0]:
        if card.attrib['id'] in encounter_cards:
            prop = _get_property(card, 'Encounter Set Total')
            prop.set('value', str(
                encounter_sets[encounter_cards[card.attrib['id']]]))

    tree.write(xml_path)
    logging.info('[%s, %s] ...Updating the Strange Eons xml file with'
                 ' additional data (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def calculate_hashes(set_id, set_name, lang):  # pylint: disable=R0914
    """ Update the Strange Eons xml file with hashes and skip flags.
    """
    logging.info('[%s, %s] Updating the Strange Eons xml file with hashes and'
                 ' skip flags...', set_name, lang)
    timestamp = time.time()

    new_path = os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id, lang))
    tree = ET.parse(new_path)
    root = tree.getroot()

    for card in root[0]:
        card_hash = hashlib.md5(
            re.sub(r'\n\s*', '', ET.tostring(card, encoding='unicode').strip()
                   ).encode()).hexdigest()
        card.set('hash', card_hash)

    new_file_hash = hashlib.md5(
        re.sub(r'\n\s*', '', ET.tostring(root, encoding='unicode').strip()
               ).encode()).hexdigest()
    root.set('hash', new_file_hash)

    old_file_hash = ''
    old_path = os.path.join(SET_EONS_PATH, '{}.{}.xml.old'.format(set_id,
                                                                  lang))
    if os.path.exists(old_path):
        old_hashes = {}
        skip_ids = set()

        tree_old = ET.parse(old_path)
        root_old = tree_old.getroot()
        old_file_hash = root_old.attrib['hash']
        if old_file_hash == new_file_hash:
            root.set('skip', '1')

        for card in root_old[0]:
            old_hashes[card.attrib['id']] = card.attrib['hash']

        for card in root[0]:
            if old_hashes.get(card.attrib['id']) == card.attrib['hash']:
                skip_ids.add(card.attrib['id'])
                card.set('skip', '1')

    tree.write(new_path)

    logging.info('[%s, %s] ...Updating the Strange Eons xml file with hashes'
                 ' and skip flags (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))
    return (new_file_hash, old_file_hash)


def copy_custom_images(conf, set_id, set_name):
    """ Copy custom image files into the project folder.
    """
    logging.info('[%s] Copying custom image files into the project folder...',
                 set_name)
    timestamp = time.time()

    images_path = os.path.join(_get_artwork_path(conf, set_id),
                               IMAGES_CUSTOM_FOLDER)
    if os.path.exists(images_path):
        for _, _, filenames in os.walk(images_path):
            for filename in filenames:
                if filename.split('.')[-1] not in ('jpg', 'png'):
                    continue

                output_filename = '{}_{}'.format(set_id, filename)
                shutil.copyfile(os.path.join(images_path, filename),
                                os.path.join(IMAGES_CUSTOM_PATH,
                                             output_filename))

            break

    logging.info('[%s] ...Copying custom image files into the project folder'
                 ' (%ss)', set_name, round(time.time() - timestamp, 3))


def copy_raw_images(conf, set_id, set_name, lang):
    """ Copy raw image files into the project folder.
    """
    logging.info('[%s, %s] Copying raw image files into the project folder...',
                 set_name, lang)
    timestamp = time.time()

    artwork_path = _get_artwork_path(conf, set_id)
    processed_artwork_path = os.path.join(artwork_path,
                                          PROCESSED_ARTWORK_FOLDER)
    tree = ET.parse(os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id,
                                                                   lang)))
    root = tree.getroot()
    for card in root[0]:
        if card.attrib.get('skip') != '1':
            filename = _find_properties(card, 'Artwork')
            if filename:
                filename = filename[0].attrib['value']
                if os.path.exists(os.path.join(processed_artwork_path,
                                               filename)):
                    input_path = os.path.join(processed_artwork_path, filename)
                else:
                    input_path = os.path.join(artwork_path, filename)

                output_path = os.path.join(IMAGES_RAW_PATH, filename)
                if not os.path.exists(output_path):
                    shutil.copyfile(input_path, output_path)

            alternate = [a for a in card if a.attrib.get('type') == 'B']
            if alternate:
                alternate = alternate[0]
                filename = _find_properties(alternate, 'Artwork')
                if filename:
                    filename = filename[0].attrib['value']
                    if os.path.exists(os.path.join(processed_artwork_path,
                                                   filename)):
                        input_path = os.path.join(processed_artwork_path,
                                                  filename)
                    else:
                        input_path = os.path.join(artwork_path, filename)

                    output_path = os.path.join(IMAGES_RAW_PATH, filename)
                    if not os.path.exists(output_path):
                        shutil.copyfile(input_path, output_path)

    logging.info('[%s, %s] ...Copying raw image files into the project folder'
                 ' (%ss)', set_name, lang, round(time.time() - timestamp, 3))


def copy_xml(set_id, set_name, lang):
    """ Copy the Strange Eons xml file into the project.
    """
    logging.info('[%s, %s] Copying the Strange Eons xml file into'
                 ' the project...', set_name, lang)
    timestamp = time.time()

    shutil.copyfile(os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id,
                                                                   lang)),
                    os.path.join(XML_PATH, '{}.{}.xml'.format(set_id, lang)))
    logging.info('[%s, %s] ...Copying the Strange Eons xml file into the'
                 ' project (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def create_project():
    """ Create a Strange Eons project archive.
    """
    logging.info('Creating a Strange Eons project archive...')
    timestamp = time.time()

    with zipfile.ZipFile(PROJECT_PATH, 'w') as zip_obj:
        for root, _, filenames in os.walk(PROJECT_FOLDER):
            for filename in filenames:
                zip_obj.write(os.path.join(root, filename))

    logging.info('...Creating a Strange Eons project archive (%ss)',
                 round(time.time() - timestamp, 3))


def get_skip_info(set_id, set_name, lang):
    """ Get skip information for the set and individual cards.
    """
    logging.info('[%s, %s] Getting skip information...', set_name, lang)
    timestamp = time.time()

    skip_ids = set()
    tree = ET.parse(os.path.join(SET_EONS_PATH, '{}.{}.xml'.format(set_id,
                                                                   lang)))
    root = tree.getroot()
    skip_set = root.attrib.get('skip') == '1'
    for card in root[0]:
        if card.attrib.get('skip') == '1':
            skip_ids.add(card.attrib['id'])

    logging.info('[%s, %s] ...Getting skip information (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))
    return skip_set, skip_ids


def generate_png300_db(conf, set_id, set_name, lang, skip_ids):  # pylint: disable=R0914
    """ Generate images for DB outputs.
    """
    logging.info('[%s, %s] Generating images for DB outputs...',
                 set_name, lang)
    timestamp = time.time()

    output_path = os.path.join(IMAGES_EONS_PATH, 'png300DB',
                               '{}.{}'.format(set_id, lang))
    _create_folder(output_path)
    _clear_modified_images(output_path, skip_ids)
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_png300_db.{}.{}'.format(set_id, lang))
    _create_folder(temp_path)
    _clear_folder(temp_path)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png300NoBleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            with zip_obj.open(filename) as zip_file:
                with open(os.path.join(temp_path, output_filename),
                          'wb') as output_file:
                    shutil.copyfileobj(zip_file, output_file)

    cmd = GIMP_COMMAND.format(
        conf['gimp_console_path'],
        'python-prepare-db-output-folder',
        temp_path.replace('\\', '\\\\'),
        output_path.replace('\\', '\\\\'))
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info('[%s, %s] %s', set_name, lang, res)

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating images for DB outputs'
                 ' (%ss)', set_name, lang, round(time.time() - timestamp, 3))


def generate_png300_octgn(set_id, set_name, lang, skip_ids):
    """ Generate images for OCTGN outputs.
    """
    logging.info('[%s, %s] Generating images for OCTGN outputs...',
                 set_name, lang)
    timestamp = time.time()

    output_path = os.path.join(IMAGES_EONS_PATH, 'png300OCTGN',
                               '{}.{}'.format(set_id, lang))
    _create_folder(output_path)
    _clear_modified_images(output_path, skip_ids)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png300NoBleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            with zip_obj.open(filename) as zip_file:
                with open(os.path.join(output_path, output_filename),
                          'wb') as output_file:
                    shutil.copyfileobj(zip_file, output_file)

    logging.info('[%s, %s] ...Generating images for OCTGN outputs'
                 ' (%ss)', set_name, lang, round(time.time() - timestamp, 3))


def generate_png300_pdf(conf, set_id, set_name, lang, skip_ids):  # pylint: disable=R0914
    """ Generate images for PDF outputs.
    """
    logging.info('[%s, %s] Generating images for PDF outputs...',
                 set_name, lang)
    timestamp = time.time()

    output_path = os.path.join(IMAGES_EONS_PATH, 'png300PDF',
                               '{}.{}'.format(set_id, lang))
    _create_folder(output_path)
    _clear_modified_images(output_path, skip_ids)
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_png300_pdf.{}.{}'.format(set_id, lang))
    _create_folder(temp_path)
    _clear_folder(temp_path)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png300Bleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            if output_filename.endswith('-2.png'):
                with zip_obj.open(filename) as zip_file:
                    with open(os.path.join(temp_path, output_filename),
                              'wb') as output_file:
                        shutil.copyfileobj(zip_file, output_file)

    cmd = GIMP_COMMAND.format(
        conf['gimp_console_path'],
        'python-prepare-pdf-back-folder',
        temp_path.replace('\\', '\\\\'),
        output_path.replace('\\', '\\\\'))
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info('[%s, %s] %s', set_name, lang, res)

    _clear_folder(temp_path)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png300NoBleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            if output_filename.endswith('-1.png'):
                with zip_obj.open(filename) as zip_file:
                    with open(os.path.join(temp_path, output_filename),
                              'wb') as output_file:
                        shutil.copyfileobj(zip_file, output_file)

    cmd = GIMP_COMMAND.format(
        conf['gimp_console_path'],
        'python-prepare-pdf-front-folder',
        temp_path.replace('\\', '\\\\'),
        output_path.replace('\\', '\\\\'))
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info('[%s, %s] %s', set_name, lang, res)

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating images for PDF outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def generate_png800_bleedmpc(conf, set_id, set_name, lang, skip_ids):  # pylint: disable=R0914
    """ Generate images for MakePlayingCards outputs.
    """
    logging.info('[%s, %s] Generating images for MakePlayingCards outputs...',
                 set_name, lang)
    timestamp = time.time()

    output_path = os.path.join(IMAGES_EONS_PATH, 'png800BleedMPC',
                               '{}.{}'.format(set_id, lang))
    _create_folder(output_path)
    _clear_modified_images(output_path, skip_ids)
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_png800_bleedmpc.{}.{}'.format(set_id,
                                                                     lang))
    _create_folder(temp_path)
    _clear_folder(temp_path)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png800Bleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            with zip_obj.open(filename) as zip_file:
                with open(os.path.join(temp_path, output_filename),
                          'wb') as output_file:
                    shutil.copyfileobj(zip_file, output_file)

    cmd = GIMP_COMMAND.format(
        conf['gimp_console_path'],
        'python-prepare-makeplayingcards-folder',
        temp_path.replace('\\', '\\\\'),
        output_path.replace('\\', '\\\\'))
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info('[%s, %s] %s', set_name, lang, res)

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating images for MakePlayingCards outputs'
                 ' (%ss)', set_name, lang, round(time.time() - timestamp, 3))


def generate_jpg300_bleeddtc(conf, set_id, set_name, lang, skip_ids):  # pylint: disable=R0914
    """ Generate images for DriveThruCards outputs.
    """
    logging.info('[%s, %s] Generating images for DriveThruCards outputs...',
                 set_name, lang)
    timestamp = time.time()

    output_path = os.path.join(IMAGES_EONS_PATH, 'jpg300BleedDTC',
                               '{}.{}'.format(set_id, lang))
    _create_folder(output_path)
    _clear_modified_images(output_path, skip_ids)
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_jpg300_bleeddtc.{}.{}'.format(set_id,
                                                                     lang))
    _create_folder(temp_path)
    _clear_folder(temp_path)

    with zipfile.ZipFile(PROJECT_PATH) as zip_obj:
        filelist = [f for f in zip_obj.namelist()
                    if f.startswith('{}{}'.format(IMAGES_ZIP_PATH,
                                                  'png300Bleed'))
                    and f.split('.')[-1] == 'png'
                    and f.split('.')[-2] == lang
                    and f.split('.')[-3] == set_id]
        for filename in filelist:
            output_filename = _update_zip_filename(filename)
            with zip_obj.open(filename) as zip_file:
                with open(os.path.join(temp_path, output_filename),
                          'wb') as output_file:
                    shutil.copyfileobj(zip_file, output_file)

    cmd = GIMP_COMMAND.format(
        conf['gimp_console_path'],
        'python-prepare-drivethrucards-folder',
        temp_path.replace('\\', '\\\\'),
        output_path.replace('\\', '\\\\'))
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info('[%s, %s] %s', set_name, lang, res)

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating images for DriveThruCards outputs'
                 ' (%ss)', set_name, lang, round(time.time() - timestamp, 3))


def generate_db(set_id, set_name, lang):
    """ Generate DB outputs.
    """
    logging.info('[%s, %s] Generating DB outputs...', set_name, lang)
    timestamp = time.time()

    input_path = os.path.join(IMAGES_EONS_PATH, 'png300DB',
                              '{}.{}'.format(set_id, lang))
    output_path = os.path.join(OUTPUT_DB_PATH, '{}.{}'.format(set_name, lang))

    known_filenames = set()
    for _, _, filenames in os.walk(input_path):
        if not filenames:
            logging.error('[%s, %s] ERROR: No cards found', set_name, lang)
            break

        _create_folder(output_path)
        _clear_folder(output_path)
        for filename in filenames:
            if filename.split('.')[-1] != 'png':
                continue

            output_filename = '{}-{}{}{}'.format(
                filename[:3],
                re.sub('-+$', '', filename[8:50]),
                re.sub('-1$', '', filename[86:88]),
                filename[88:])
            if output_filename not in known_filenames:
                known_filenames.add(output_filename)
                shutil.copyfile(os.path.join(input_path, filename),
                                os.path.join(output_path, output_filename))

        break

    logging.info('[%s, %s] ...Generating DB outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def generate_octgn(set_id, set_name, lang):
    """ Generate OCTGN outputs.
    """
    logging.info('[%s, %s] Generating OCTGN outputs...', set_name, lang)
    timestamp = time.time()

    input_path = os.path.join(IMAGES_EONS_PATH, 'png300OCTGN',
                              '{}.{}'.format(set_id, lang))
    output_path = os.path.join(OUTPUT_OCTGN_PATH, set_name)
    pack_path = os.path.join(output_path, '{}.{}.o8c'.format(set_name, lang))

    known_filenames = set()
    for _, _, filenames in os.walk(input_path):
        if not filenames:
            logging.error('[%s, %s] ERROR: No cards found', set_name, lang)
            break

        _create_folder(output_path)
        with zipfile.ZipFile(pack_path, 'w') as zip_obj:
            for filename in filenames:
                if filename.split('.')[-1] != 'png':
                    continue

                octgn_filename = re.sub(
                    r'-1\.png$', '.png',
                    re.sub(r'-2\.png$', '.B.png', filename))[50:]
                if octgn_filename not in known_filenames:
                    known_filenames.add(octgn_filename)
                    zip_obj.write(os.path.join(input_path, filename),
                                  '{}/{}/Cards/{}'.format(OCTGN_ZIP_PATH,
                                                          set_id,
                                                          octgn_filename))

        break

    logging.info('[%s, %s] ...Generating OCTGN outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def _collect_pdf_images(input_path):
    """ Collect image filenames for generated PDF.
    """
    for _, _, filenames in os.walk(input_path):
        if not filenames:
            return {}

        images = {'player': [],
                  'encounter': [],
                  'custom': []}
        for filename in filenames:
            parts = filename.split('-')
            if parts[-1] != '1.png':
                continue

            back_path = os.path.join(input_path, '{}-2.png'.format(
                '-'.join(parts[:-1])))
            if os.path.exists(back_path):
                back_type = 'custom'
            else:
                if parts[2] == 'p':
                    back_type = 'player'
                    back_path = os.path.join(IMAGES_BACK_PATH,
                                             'playerBackOfficial.png')
                elif parts[2] == 'e':
                    back_type = 'encounter'
                    back_path = os.path.join(IMAGES_BACK_PATH,
                                             'encounterBackOfficial.png')
                else:
                    logging.error('ERROR: Missing card back for %s, removing'
                                  ' the file', filename)
                    continue

            copies = 3 if parts[1] == 'p' else 1
            for _ in range(copies):
                images[back_type].append((
                    os.path.join(input_path, filename), back_path))

        break

    return images


def generate_pdf(set_id, set_name, lang):  # pylint: disable=R0914
    """ Generate PDF outputs.
    """
    logging.info('[%s, %s] Generating PDF outputs...', set_name, lang)
    timestamp = time.time()

    input_path = os.path.join(IMAGES_EONS_PATH, 'png300PDF',
                              '{}.{}'.format(set_id, lang))
    output_path = os.path.join(OUTPUT_PDF_PATH, '{}.{}'.format(set_name, lang))

    images = _collect_pdf_images(input_path)
    if not images:
        logging.error('[%s, %s] ERROR: No cards found', set_name, lang)
        logging.info('[%s, %s] ...Generating PDF outputs (%ss)',
                     set_name, lang, round(time.time() - timestamp, 3))
        return

    _create_folder(output_path)
    pages_raw = []
    for key in images:
        pages_raw.extend([(images[key][i * 6:(i + 1) * 6] + [None] * 6)[:6]
                          for i in range(math.ceil(len(images[key]) / 6))])

    pages = []
    for page in pages_raw:
        front_page = [i and i[0] or None for i in page]
        back_page = [i and i[1] or None for i in page]
        back_page = [back_page[2], back_page[1], back_page[0],
                     back_page[5], back_page[4], back_page[3]]
        pages.extend([front_page, back_page])

    formats = {'A4': A4, 'Letter': letter}
    card_width = 2.75 * inch
    card_height = 3.75 * inch

    for page_format in formats:
        canvas = Canvas(
            os.path.join(output_path, '{}.{}.{}.pdf'.format(page_format,
                                                            set_name, lang)),
            pagesize=landscape(formats[page_format]))
        width, height = landscape(formats[page_format])
        width_margin = (width - 3 * card_width) / 2
        height_margin = (height - 2 * card_height) / 2
        for page in pages:
            for i, card in enumerate(page):
                if card:
                    width_pos = (
                        width_margin + i * card_width
                        if i < 6 / 2
                        else width_margin + (i - 6 / 2) * card_width)
                    height_pos = (height_margin + card_height
                                  if i < 6 / 2
                                  else height_margin)
                    canvas.drawImage(card, width_pos, height_pos,
                                     card_width, card_height, anchor='sw')

            canvas.showPage()

        canvas.save()

    logging.info('[%s, %s] ...Generating PDF outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def _insert_png_text(filepath, text):
    """ Insert text into a PNG file.
    """
    reader = png.Reader(filename=filepath)
    chunk_list = list(reader.chunks())
    chunk_item = tuple([TEXT_CHUNK_FLAG, bytes(text, 'utf-8')])
    chunk_list.insert(1, chunk_item)
    with open(filepath, 'wb') as obj:
        png.write_chunks(obj, chunk_list)


def _make_unique_png(input_path):
    """ Make unique PNG files for MakePlayingCards.
    """
    for _, _, filenames in os.walk(input_path):
        for filename in filenames:
            if filename.endswith('-1.png') or filename.endswith('-2.png'):
                _insert_png_text(os.path.join(input_path, filename), filename)

        break


def _make_cmyk(conf, input_path):
    """ Convert RGB to CMYK for DriveThruCards.
    """
    cmd = MAGICK_COMMAND.format(conf['magick_path'], input_path)
    res = subprocess.run(cmd, capture_output=True, shell=True, check=True)
    logging.info(res)


def _prepare_printing_images(input_path, output_path, service):
    """ Prepare images for MakePlayingCards/DriveThruCards.
    """
    file_type = 'png' if service == 'mpc' else 'jpg'
    for _, _, filenames in os.walk(input_path):
        for filename in filenames:
            parts = filename.split('-')
            if parts[-1] not in '1.{}'.format(file_type):
                continue

            back_path = os.path.join(input_path, '{}-2.{}'.format(
                '-'.join(parts[:-1]), file_type))
            if not os.path.exists(back_path):
                if parts[2] == 'p':
                    back_path = os.path.join(
                        IMAGES_BACK_PATH,
                        service == 'mpc' and 'playerBackUnofficialMPC.png'
                        or 'playerBackOfficialDTC.jpg')
                elif parts[2] == 'e':
                    back_path = os.path.join(
                        IMAGES_BACK_PATH,
                        service == 'mpc' and 'encounterBackUnofficialMPC.png'
                        or 'encounterBackOfficialDTC.jpg')
                else:
                    logging.error('ERROR: Missing card back for %s, removing'
                                  ' the file', filename)
                    continue

            if parts[1] == 'p':
                for i in range(3):
                    parts[1] = str(i + 1)
                    front_output_path = os.path.join(
                        output_path, re.sub(
                            r'-(?:e|p)-', '-',
                            re.sub('-+', '-',
                                   re.sub(r'.{36}(?=-1\.(?:png|jpg))', '',
                                          '-'.join(parts)))))
                    back_output_path = os.path.join(
                        output_path, re.sub(
                            r'-(?:e|p)-', '-',
                            re.sub('-+', '-',
                                   re.sub(r'.{36}(?=-2\.(?:png|jpg))', '',
                                          '{}-2.{}'.format(
                                              '-'.join(parts[:-1]),
                                              file_type)))))
                    shutil.copyfile(os.path.join(input_path, filename),
                                    front_output_path)
                    shutil.copyfile(back_path, back_output_path)

            else:
                front_output_path = os.path.join(
                    output_path, re.sub(
                        r'-(?:e|p)-', '-',
                        re.sub('-+', '-',
                               re.sub(r'.{36}(?=-1\.(?:png|jpg))', '',
                                      '-'.join(parts)))))
                back_output_path = os.path.join(
                    output_path, re.sub(
                        r'-(?:e|p)-', '-',
                        re.sub('-+', '-',
                               re.sub(r'.{36}(?=-2\.(?:png|jpg))', '',
                                      '{}-2.{}'.format(
                                          '-'.join(parts[:-1]),
                                          file_type)))))
                shutil.copyfile(os.path.join(input_path, filename),
                                front_output_path)
                shutil.copyfile(back_path, back_output_path)

        break


def _prepare_mpc_printing_archive(input_path, obj):
    """ Prepare archive for MakePlayingCards.
    """
    for _, _, filenames in os.walk(input_path):
        for filename in filenames:
            if filename.endswith('-1.png'):
                obj.write(os.path.join(input_path, filename),
                          'front/{}'.format(filename))
            elif filename.endswith('-2.png'):
                obj.write(os.path.join(input_path, filename),
                          'back/{}'.format(filename))

        break


def _deck_name(current_cnt, total_cnt):
    """ Get deck name for DriveThruCards.
    """
    if total_cnt > 130:
        return 'deck{}/'.format(min(math.floor((current_cnt - 1) / 120) + 1,
                                    math.ceil((total_cnt - 10) / 120)))

    return ''


def _prepare_dtc_printing_archive(input_path, obj):
    """ Prepare archive for DriveThruCards.
    """
    for _, _, filenames in os.walk(input_path):
        front_cnt = 0
        back_cnt = 0
        filenames = sorted(f for f in filenames if f.endswith('-1.jpg')
                           or f.endswith('-2.jpg'))
        total_cnt = len(filenames) / 2
        for filename in filenames:
            if filename.endswith('-1.jpg'):
                front_cnt += 1
                obj.write(os.path.join(input_path, filename),
                          '{}front/{}'.format(_deck_name(front_cnt, total_cnt),
                                              filename))
            elif filename.endswith('-2.jpg'):
                back_cnt += 1
                obj.write(os.path.join(input_path, filename),
                          '{}back/{}'.format(_deck_name(back_cnt, total_cnt),
                                             filename))

        break


def generate_mpc(conf, set_id, set_name, lang):
    """ Generate MakePlayingCards outputs.
    """
    logging.info('[%s, %s] Generating MakePlayingCards outputs...',
                 set_name, lang)
    timestamp = time.time()

    input_path = os.path.join(IMAGES_EONS_PATH, 'png800BleedMPC',
                              '{}.{}'.format(set_id, lang))
    output_path = os.path.join(OUTPUT_MPC_PATH, '{}.{}'.format(set_name, lang))
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_mpc.{}.{}'.format(set_id, lang))

    for _, _, filenames in os.walk(input_path):
        if not filenames:
            logging.error('[%s, %s] ERROR: No cards found', set_name, lang)
            logging.info('[%s, %s] ...Generating MakePlayingCards outputs '
                         '(%ss)',
                         set_name, lang, round(time.time() - timestamp, 3))
            return

        break

    _create_folder(output_path)
    _create_folder(temp_path)
    _clear_folder(temp_path)
    _prepare_printing_images(input_path, temp_path, 'mpc')
    _make_unique_png(temp_path)

    if 'makeplayingcards_zip' in conf['outputs']:
        with zipfile.ZipFile(
                os.path.join(output_path,
                             'MPC.{}.{}.zip'.format(set_name, lang)),
                'w') as obj:
            _prepare_mpc_printing_archive(temp_path, obj)
            obj.write('MakePlayingCards.pdf', 'MakePlayingCards.pdf')

    if 'makeplayingcards_7z' in conf['outputs']:
        with py7zr.SevenZipFile(
                os.path.join(output_path,
                             'MPC.{}.{}.7z'.format(set_name, lang)),
                'w') as obj:
            _prepare_mpc_printing_archive(temp_path, obj)
            obj.write('MakePlayingCards.pdf', 'MakePlayingCards.pdf')

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating MakePlayingCards outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def generate_dtc(conf, set_id, set_name, lang):
    """ Generate DriveThruCards outputs.
    """
    logging.info('[%s, %s] Generating DriveThruCards outputs...',
                 set_name, lang)
    timestamp = time.time()

    input_path = os.path.join(IMAGES_EONS_PATH, 'jpg300BleedDTC',
                              '{}.{}'.format(set_id, lang))
    output_path = os.path.join(OUTPUT_DTC_PATH, '{}.{}'.format(set_name, lang))
    temp_path = os.path.join(TEMP_ROOT_PATH,
                             'generate_dtc.{}.{}'.format(set_id, lang))

    for _, _, filenames in os.walk(input_path):
        if not filenames:
            logging.error('[%s, %s] ERROR: No cards found', set_name, lang)
            logging.info('[%s, %s] ...Generating DriveThruCards outputs (%ss)',
                         set_name, lang, round(time.time() - timestamp, 3))
            return

        break

    _create_folder(output_path)
    _create_folder(temp_path)
    _clear_folder(temp_path)
    _prepare_printing_images(input_path, temp_path, 'dtc')
    _make_cmyk(conf, temp_path)

    if 'drivethrucards_zip' in conf['outputs']:
        with zipfile.ZipFile(
                os.path.join(output_path,
                             'DTC.{}.{}.zip'.format(set_name, lang)),
                'w') as obj:
            _prepare_dtc_printing_archive(temp_path, obj)
            obj.write('DriveThruCards.pdf', 'DriveThruCards.pdf')

    if 'drivethrucards_7z' in conf['outputs']:
        with py7zr.SevenZipFile(
                os.path.join(output_path,
                             'DTC.{}.{}.7z'.format(set_name, lang)),
                'w') as obj:
            _prepare_dtc_printing_archive(temp_path, obj)
            obj.write('DriveThruCards.pdf', 'DriveThruCards.pdf')

    _delete_folder(temp_path)
    logging.info('[%s, %s] ...Generating DriveThruCards outputs (%ss)',
                 set_name, lang, round(time.time() - timestamp, 3))


def _create_octgn_archive(temp_path):
    """ Create OCTGN archive with all set.xml files.
    """
    archive_path = os.path.join(temp_path, OCTGN_ARCHIVE)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as obj:
        for _, folders, _ in os.walk(OUTPUT_OCTGN_PATH):
            for folder in folders:
                for _, subfolders, _ in os.walk(
                        os.path.join(OUTPUT_OCTGN_PATH, folder)):
                    for subfolder in subfolders:
                        xml_path = os.path.join(OUTPUT_OCTGN_PATH, folder,
                                                subfolder, 'set.xml')
                        if os.path.exists(xml_path):
                            obj.write(xml_path, '{}/set.xml'.format(subfolder))

                    break

            break


def _prepare_updated_o8c(temp_path, updates):
    """ Copy all updated o8c files to the temporary folder.
    """
    for _, folders, _ in os.walk(OUTPUT_OCTGN_PATH):
        for folder in folders:
            for _, _, filenames in os.walk(
                    os.path.join(OUTPUT_OCTGN_PATH, folder)):
                for filename in filenames:
                    parts = filename.split('.')
                    if len(parts) != 3:
                        continue

                    if (parts[0], parts[1]) in updates:
                        shutil.copyfile(os.path.join(OUTPUT_OCTGN_PATH,
                                                     folder, filename),
                                        os.path.join(temp_path, filename))

                break

        break


def copy_octgn_outputs(conf, copy_o8c=False, updates=None):
    """ Copy OCTGN outputs to the destination folder.
    """
    logging.info('Copying OCTGN outputs to the destination folder...')
    timestamp = time.time()

    temp_path = os.path.join(TEMP_ROOT_PATH, 'copy_octgn_outputs')
    _create_folder(temp_path)
    _clear_folder(temp_path)
    _create_octgn_archive(temp_path)

    if copy_o8c and updates:
        _prepare_updated_o8c(temp_path, updates)

    for _, _, filenames in os.walk(temp_path):
        for filename in filenames:
            shutil.move(os.path.join(temp_path, filename),
                        os.path.join(conf['octgn_destination_path'], filename))

        break

    _delete_folder(temp_path)
    logging.info('...Copying OCTGN outputs to the destination folder (%ss)',
                 round(time.time() - timestamp, 3))
