"""The 13 Hydrogen-codec carts (No-Intro names), short ids and player build ids."""
import os
ROMDIR = os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'roms')
CARTS = {
    'MGUE': 'All Grown Up! - Volume 1 (USA)', 'MSHE': 'Sonic X - Volume 1 (USA)',
    'MYGE': 'Yu-Gi-Oh! - Yugi vs. Joey (USA, Europe)', 'MSSE': 'SpongeBob SquarePants - Volume 1 (USA) (Rev 1)',
    'MS2E': 'SpongeBob SquarePants - Volume 2 (USA) (Rev 1)', 'MFOE': 'The Fairly OddParents! - Volume 1 (USA)',
    'MF2E': 'The Fairly OddParents! - Volume 2 (USA) (Rev 1)', 'MTME': 'Teenage Mutant Ninja Turtles - Things Change (USA, Europe)',
    'MNCE': 'Nicktoons Collection - Volume 1 (USA)', 'MSBE': 'Strawberry Shortcake - Volume 1 (USA)',
    'MKDE': 'Codename - Kids Next Door - Volume 1 (USA, Europe)', 'MCTE': 'Cartoon Network Collection - Volume 1 (USA, Europe)',
    'MDRE': 'Dora the Explorer - Volume 1 (USA)'}
BUILD = {'MGUE': '04cbcc07fe42', 'MSHE': '04cbcc07fe42', 'MFOE': '1c25494320ba', 'MSSE': '92e428ed134d', 'MS2E': '92e428ed134d',
         'MF2E': '92e428ed134d', 'MTME': '93fe5e2e96b3', 'MNCE': 'c1313a3736b4', 'MSBE': 'c3b335b0b286', 'MKDE': 'dff48b69f680',
         'MCTE': 'f5f0564f89e3', 'MYGE': 'f73d177a8155', 'MDRE': 'ff6e0e4279e0'}
SHORT = {'MGUE': 'agu1', 'MSHE': 'sonicx1', 'MYGE': 'yugioh', 'MSSE': 'sponge1', 'MS2E': 'sponge2', 'MFOE': 'fop1',
         'MF2E': 'fop2', 'MTME': 'tmnt', 'MNCE': 'nick1', 'MSBE': 'strawb1', 'MKDE': 'knd1', 'MCTE': 'cn1', 'MDRE': 'dora1'}
def rom_path(code):
    return os.path.join(ROMDIR, 'Game Boy Advance Video - ' + CARTS[code] + '.gba')
