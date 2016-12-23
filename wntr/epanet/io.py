# -*- coding: latin-1 -*-
"""
Provides classes for reading/writing EPANET input and output files.
"""
from __future__ import absolute_import
import wntr.network
from wntr.network import WaterNetworkModel, Junction, Reservoir, Tank, Pipe, Pump, Valve
import wntr
import io

from .util import FlowUnits, MassUnits, HydParam, QualParam
from .util import LinkBaseStatus

import datetime
import networkx as nx
import re
import logging
import numpy as np

logger = logging.getLogger(__name__)

_INP_SECTIONS = ['[OPTIONS]', '[TITLE]', '[JUNCTIONS]', '[RESERVOIRS]',
                 '[TANKS]', '[PIPES]', '[PUMPS]', '[VALVES]', '[EMITTERS]',
                 '[CURVES]', '[PATTERNS]', '[ENERGY]', '[STATUS]',
                 '[CONTROLS]', '[RULES]', '[DEMANDS]', '[QUALITY]',
                 '[REACTIONS]', '[SOURCES]', '[MIXING]',
                 '[TIMES]', '[REPORT]', '[COORDINATES]', '[VERTICES]',
                 '[LABELS]', '[BACKDROP]', '[TAGS]']

_JUNC_ENTRY = ' {name:20} {elev:12.6g} {dem:12.6g} {pat:24} {com:>3s}\n'
_JUNC_LABEL = '{:21} {:>12s} {:>12s} {:24}\n'

_RES_ENTRY = ' {name:20s} {head:12.6g} {pat:>24s} {com:>3s}\n'
_RES_LABEL = '{:21s} {:>12s} {:>24s}\n'

_TANK_ENTRY = ' {name:20s} {elev:12.6g} {initlev:12.6g} {minlev:12.6g} {maxlev:12.6g} {diam:12.6g} {minvol:12.6g} {curve:20s} {com:>3s}\n'
_TANK_LABEL = '{:21s} {:>12s} {:>12s} {:>12s} {:>12s} {:>12s} {:>12s} {:20s}\n'

_PIPE_ENTRY = ' {name:20s} {node1:20s} {node2:20s} {len:12.6g} {diam:12.6g} {rough:12.6g} {mloss:12.6g} {status:>20s} {com:>3s}\n'
_PIPE_LABEL = '{:21s} {:20s} {:20s} {:>12s} {:>12s} {:>12s} {:>12s} {:>20s}\n'

_PUMP_ENTRY = ' {name:20s} {node1:20s} {node2:20s} {ptype:8s} {params:20s} {com:>3s}\n'
_PUMP_LABEL = '{:21s} {:20s} {:20s} {:20s}\n'

_VALVE_ENTRY = ' {name:20s} {node1:20s} {node2:20s} {diam:12.6g} {vtype:4s} {set:12.6g} {mloss:12.6g} {com:>3s}\n'
_VALVE_LABEL = '{:21s} {:20s} {:20s} {:>12s} {:4s} {:>12s} {:>12s}\n'

_CURVE_ENTRY = ' {name:10s} {x:12f} {y:12f} {com:>3s}\n'
_CURVE_LABEL = '{:11s} {:12s} {:12s}\n'



def _is_number(s):
    """
    Checks if imput is a number


    Parameters
    ----------
    s : anything

    """

    try:
        float(s)
        return True
    except ValueError:
        return False


def _str_time_to_sec(s):
    """
    Converts epanet time format to seconds.


    Parameters
    ----------
    s : string
        EPANET time string. Options are 'HH:MM:SS', 'HH:MM', 'HH'


    Returns
    -------
     Integer value of time in seconds.
    """
    pattern1 = re.compile(r'^(\d+):(\d+):(\d+)$')
    time_tuple = pattern1.search(s)
    if bool(time_tuple):
        return (int(time_tuple.groups()[0])*60*60 +
                int(time_tuple.groups()[1])*60 +
                int(round(float(time_tuple.groups()[2]))))
    else:
        pattern2 = re.compile(r'^(\d+):(\d+)$')
        time_tuple = pattern2.search(s)
        if bool(time_tuple):
            return (int(time_tuple.groups()[0])*60*60 +
                    int(time_tuple.groups()[1])*60)
        else:
            pattern3 = re.compile(r'^(\d+)$')
            time_tuple = pattern3.search(s)
            if bool(time_tuple):
                return int(time_tuple.groups()[0])*60*60
            else:
                raise RuntimeError("Time format in "
                                   "INP file not recognized. ")


def _clock_time_to_sec(s, am_pm):
    """
    Converts epanet clocktime format to seconds.


    Parameters
    ----------
    s : string
        EPANET time string. Options are 'HH:MM:SS', 'HH:MM', HH'

    am : string
        options are AM or PM


    Returns
    -------
    Integer value of time in seconds

    """
    if am_pm.upper() == 'AM':
        am = True
    elif am_pm.upper() == 'PM':
        am = False
    else:
        raise RuntimeError('am_pm option not recognized; options are AM or PM')

    pattern1 = re.compile(r'^(\d+):(\d+):(\d+)$')
    time_tuple = pattern1.search(s)
    if bool(time_tuple):
        time_sec = (int(time_tuple.groups()[0])*60*60 +
                    int(time_tuple.groups()[1])*60 +
                    int(round(float(time_tuple.groups()[2]))))
        if not am:
            time_sec += 3600*12
        if s.startswith('12'):
            time_sec -= 3600*12
        return time_sec
    else:
        pattern2 = re.compile(r'^(\d+):(\d+)$')
        time_tuple = pattern2.search(s)
        if bool(time_tuple):
            time_sec = (int(time_tuple.groups()[0])*60*60 +
                        int(time_tuple.groups()[1])*60)
            if not am:
                time_sec += 3600*12
            if s.startswith('12'):
                time_sec -= 3600*12
            return time_sec
        else:
            pattern3 = re.compile(r'^(\d+)$')
            time_tuple = pattern3.search(s)
            if bool(time_tuple):
                time_sec = int(time_tuple.groups()[0])*60*60
                if not am:
                    time_sec += 3600*12
                if s.startswith('12'):
                    time_sec -= 3600*12
                return time_sec
            else:
                raise RuntimeError("Time format in "
                                   "INP file not recognized. ")


def _sec_to_string(sec):
    hours = int(sec/3600.)
    sec -= hours*3600
    mm = int(sec/60.)
    sec -= mm*60
    return (hours, mm, int(sec))



class InpFile(object):
    """An EPANET input (.inp) file reader and writer.

    EPANET has two possible formats for its input files. The first, a NET file, is binary
    formatted, and cannot be used from the command line. The second, the INP file,
    is text formatted and easily human (and machine) readable. This class provides read
    and write functionality for INP files within WNTR.

    There are numerous sections of the INP file that are not used by WNTR. For example,
    WNTR does not perform energy calculations. Sections that are not used by WNTR are
    stored within this object as unmodified text strings. In order to ensure that a new
    INP file has all options that were read in, the user must use the same object for both
    reading and writing an INP file; if, of course, the INP file was used to create the
    network in the first place. If a new object is used solely to provide a writer for INP files,
    the INP file created will still be a valid EPANET input file, but will not have any sections
    that are not used by WNTR.

    The sections that are currently not modified by WNTR are

    * *ENERGY*
    * *RULES*
    * *DEMANDS*
    * *QUALITY*
    * *EMITTERS*
    * *SOURCES*
    * *MIXING*
    * *VERTICES*
    * *LABELS*
    * *BACKDROP*
    * *TAGS*

    In addition to storing these lines, the top-of-file comments are stored, and the original
    flow units and mass units are stored for easy conversion back to the same units that were
    read in. The output EPANET units can be changed during the ``write`` function call.

    The EPANET Users Manual provides full documentation for the INP file format in its Appendix C.

    """
    def __init__(self):
        self.sections = {}
        for sec in _INP_SECTIONS:
            self.sections[sec] = []
        self.mass_units = None
        self.flow_units = None
        self.top_comments = []
        self.curves = {}

    def read(self, filename, wn=None):
        """Method to read EPANET INP file and load data into a water network object.

        Parameters
        ----------
        filename : str
            An EPANET INP input file.


        Returns
        -------
        :class:`wntr.network.WaterNetworkModel.WaterNetworkModel`
            A WNTR network model object

        """
        if wn is None:
            wn = WaterNetworkModel()
        wn.name = filename
        opts = wn.options

        _patterns = {}
        self.curves = {}
        self.top_comments = []
        self.sections = {}
        for sec in _INP_SECTIONS:
            self.sections[sec] = []
        self.mass_units = None
        self.flow_units = None

        def split_line(line):
            _vc = line.split(';', 1)
            _cmnt = None
            _vals = None
            if len(_vc) == 0:
                pass
            elif len(_vc) == 1:
                _vals = _vc[0].split()
            elif _vc[0] == '':
                _cmnt = _vc[1]
            else:
                _vals = _vc[0].split()
                _cmnt = _vc[1]
            return _vals, _cmnt

        section = None
        lnum = 0
        edata = {'fname': filename}
        with io.open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                lnum += 1
                edata['lnum'] = lnum
                line = line.strip()
                nwords = len(line.split())
                if len(line) == 0 or nwords == 0:
                    # Blank line
                    continue
                elif line.startswith('['):
                    vals = line.split(None, 1)
                    sec = vals[0].upper()
                    edata['sec'] = sec
                    if sec in _INP_SECTIONS:
                        section = sec
                        logger.info('%(fname)s:%(lnum)-6d %(sec)13s section found' % edata)
                        continue
                    elif sec == '[END]':
                        logger.info('%(fname)s:%(lnum)-6d %(sec)13s end of file found' % edata)
                        section = None
                        break
                    else:
                        raise RuntimeError('%(fname)s:%(lnum)d: Invalid section "%(sec)s"' % edata)
                elif section is None and line.startswith(';'):
                    self.top_comments.append(line[1:])
                    continue
                elif section is None:
                    logger.debug('Found confusing line: %s', repr(line))
                    raise RuntimeError('%(fname)s:%(lnum)d: Non-comment outside of valid section!' % edata)
                # We have text, and we are in a section
                self.sections[section].append((lnum, line))

        # Parse each of the sections
        for lnum, line in self.sections['[OPTIONS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[OPTIONS]'
            words, comments = split_line(line)
            if words is not None and len(words) > 0:
                if len(words) < 2:
                    edata['key'] = words[0]
                    raise RuntimeError('%(fname)s:%(lnum)-6d %(sec)13s no value provided for %(key)s' % edata)
                key = words[0].upper()
                if key == 'UNITS':
                    self.flow_units = FlowUnits[words[1].upper()]
                    opts.units = words[1].upper()
                elif key == 'HEADLOSS':
                    opts.headloss = words[1].upper()
                elif key == 'HYDRAULICS':
                    opts.hydraulics_option = words[1].upper()
                    opts.hydraulics_filename = words[2]
                elif key == 'QUALITY':
                    opts.quality_option = words[1].upper()
                    if len(words) > 2:
                        opts.quality_value = words[2]
                        if 'ug' in words[2]:
                            self.mass_units = MassUnits.mg
                        else:
                            self.mass_units = MassUnits.ug
                    else:
                        self.mass_units = MassUnits.mg
                        opts.quality_value = 'mg/L'
                elif key == 'VISCOSITY':
                    opts.viscosity = float(words[1])
                elif key == 'DIFFUSIVITY':
                    opts.diffusivity = float(words[1])
                elif key == 'SPECIFIC':
                    opts.specific_gravity = float(words[2])
                elif key == 'TRIALS':
                    opts.trials = int(words[1])
                elif key == 'ACCURACY':
                    opts.accuracy = float(words[1])
                elif key == 'UNBALANCED':
                    opts.unbalanced_option = words[1].upper()
                    if len(words) > 2:
                        opts.unbalanced_value = int(words[2])
                elif key == 'PATTERN':
                    opts.pattern = words[1]
                elif key == 'DEMAND':
                    if len(words) > 2:
                        opts.demand_multiplier = float(words[2])
                    else:
                        edata['key'] = 'DEMAND MULTIPLIER'
                        raise RuntimeError('%(fname)s:%(lnum)-6d %(sec)13s no value provided for %(key)s' % edata)
                elif key == 'EMITTER':
                    if len(words) > 2:
                        opts.emitter_exponent = float(words[2])
                    else:
                        edata['key'] = 'EMITTER EXPONENT'
                        raise RuntimeError('%(fname)s:%(lnum)-6d %(sec)13s no value provided for %(key)s' % edata)
                elif key == 'TOLERANCE':
                    opts.tolerance = float(words[1])
                elif key == 'CHECKFREQ':
                    opts.tolerance = float(words[1])
                elif key == 'MAXCHECK':
                    opts.tolerance = float(words[1])
                elif key == 'DAMPLIMIT':
                    opts.tolerance = float(words[1])
                elif key == 'MAP':
                    opts.map = words[1]
                else:
                    if len(words) == 2:
                        edata['key'] = words[0]
                        setattr(opts, words[0].lower(), float(words[1]))
                        logger.warn('%(fname)s:%(lnum)-6d %(sec)13s option "%(key)s" is undocumented; adding, but please verify syntax', edata)
                    elif len(words) == 3:
                        edata['key'] = words[0] + ' ' + words[1]
                        setattr(opts, words[0].lower() + '_' + words[1].lower(), float(words[2]))
                        logger.warn('%(fname)s:%(lnum)-6d %(sec)13s option "%(key)s" is undocumented; adding, but please verify syntax', edata)

        inp_units = self.flow_units
        mass_units = self.mass_units

        if (type(opts.report_timestep) == float or
                type(opts.report_timestep) == int):
            if opts.report_timestep < opts.hydraulic_timestep:
                raise RuntimeError('opts.report_timestep must be greater than or equal to opts.hydraulic_timestep.')
            if opts.report_timestep % opts.hydraulic_timestep != 0:
                raise RuntimeError('opts.report_timestep must be a multiple of opts.hydraulic_timestep')

        for lnum, line in self.sections['[CURVES]']:
            # It should be noted carefully that these lines are never directly
            # applied to the WaterNetworkModel object. Because different curve
            # types are treated differently, each of the curves are converted
            # the first time they are used, and this is used to build up a
            # dictionary for those conversions to take place.
            edata['lnum'] = lnum
            edata['sec'] = '[CURVES]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            curve_name = current[0]
            if curve_name not in self.curves:
                self.curves[curve_name] = []
            self.curves[curve_name].append((float(current[1]),
                                             float(current[2])))

        for lnum, line in self.sections['[PATTERNS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[PATTERNS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            pattern_name = current[0]
            if pattern_name not in _patterns:
                _patterns[pattern_name] = []
                for i in current[1:]:
                    _patterns[pattern_name].append(float(i))
            else:
                for i in current[1:]:
                    _patterns[pattern_name].append(float(i))

        for pattern_name, pattern_list in _patterns.items():
            wn.add_pattern(pattern_name, pattern_list)

        for lnum, line in self.sections['[JUNCTIONS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[JUNCTIONS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if len(current) == 3:
                wn.add_junction(current[0],
                                HydParam.Demand.to_si(inp_units, float(current[2])),
                                None,
                                HydParam.Elevation.to_si(inp_units, float(current[1])))
            else:
                wn.add_junction(current[0],
                                HydParam.Demand.to_si(inp_units, float(current[2])),
                                current[3],
                                HydParam.Elevation.to_si(inp_units, float(current[1])))

        for lnum, line in self.sections['[RESERVOIRS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[RESERVOIRS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if len(current) == 2:
                wn.add_reservoir(current[0],
                                 HydParam.HydraulicHead.to_si(inp_units, float(current[1])))
            else:
                wn.add_reservoir(current[0],
                                 HydParam.HydraulicHead.to_si(inp_units, float(current[1])),
                                 current[2])
                logger.warn('%(fname)s:%(lnum)-6d %(sec)13s reservoir head patterns only supported in EpanetSimulator', edata)

        for lnum, line in self.sections['[TANKS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[TANKS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if len(current) == 8:  # Volume curve provided
                if float(current[6]) != 0:
                    logger.warn('%(fname)s:%(lnum)-6d %(sec)13s minimum tank volume is only available using EpanetSimulator; others use minimum level and cylindrical tanks.', edata)
                logger.warn('<%(fname)s:%(sec)s:%(line)d> tank volume curves only supported in EpanetSimulator', edata)
                curve_name = current[7]
                curve_points = []
                for point in self.curves[curve_name]:
                    x = HydParam.Length.to_si(inp_units, point[0])
                    y = HydParam.Volume.to_si(inp_units, point[1])
                    curve_points.append((x, y))
                wn.add_curve(curve_name, 'VOLUME', curve_points)
                curve = wn.get_curve(curve_name)
                wn.add_tank(current[0],
                            HydParam.Elevation.to_si(inp_units, float(current[1])),
                            HydParam.Length.to_si(inp_units, float(current[2])),
                            HydParam.Length.to_si(inp_units, float(current[3])),
                            HydParam.Length.to_si(inp_units, float(current[4])),
                            HydParam.TankDiameter.to_si(inp_units, float(current[5])),
                            HydParam.Volume.to_si(inp_units, float(current[6])),
                            curve)
            elif len(current) == 7:  # No volume curve provided
                if float(current[6]) != 0:
                    logger.warn('%(fname)s:%(lnum)-6d %(sec)13s minimum tank volume is only available using EpanetSimulator; others use minimum level and cylindrical tanks.', edata)
                wn.add_tank(current[0],
                            HydParam.Elevation.to_si(inp_units, float(current[1])),
                            HydParam.Length.to_si(inp_units, float(current[2])),
                            HydParam.Length.to_si(inp_units, float(current[3])),
                            HydParam.Length.to_si(inp_units, float(current[4])),
                            HydParam.TankDiameter.to_si(inp_units, float(current[5])),
                            HydParam.Volume.to_si(inp_units, float(current[6])))
            else:
                edata['line'] = line
                logger.error('%(fname)s:%(lnum)-6d %(sec)13s tank entry format not recognized: "%(line)s"', edata)
                raise RuntimeError('Tank entry format not recognized.')

        for lnum, line in self.sections['[PIPES]']:
            edata['lnum'] = lnum
            edata['sec'] = '[PIPES]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if float(current[6]) != 0:
                logger.warn('%(fname)s:%(lnum)-6d %(sec)13s non-zero minor losses only supported in EpanetSimulator', edata)
            if current[7].upper() == 'CV':
                wn.add_pipe(current[0],
                            current[1],
                            current[2],
                            HydParam.Length.to_si(inp_units, float(current[3])),
                            HydParam.PipeDiameter.to_si(inp_units, float(current[4])),
                            float(current[5]),
                            float(current[6]),
                            'OPEN',
                            True)
            else:
                wn.add_pipe(current[0],
                            current[1],
                            current[2],
                            HydParam.Length.to_si(inp_units, float(current[3])),
                            HydParam.PipeDiameter.to_si(inp_units, float(current[4])),
                            float(current[5]),
                            float(current[6]),
                            current[7].upper())

        for lnum, line in self.sections['[PUMPS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[PUMPS]'
            edata['line'] = line
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            # Only add head curves for pumps
            if current[3].upper() == 'SPEED':
                logger.warning('%(fname)s:%(lnum)-6d %(sec)13s speed settings for pumps are currently only supported in the EpanetSimulator.', edata)
                continue
            elif current[3].upper() == 'PATTERN':
                logger.warning('%(fname)s:%(lnum)-6d %(sec)13s speed patterns for pumps are currently only supported in the EpanetSimulator.', edata)
                continue
            elif current[3].upper() == 'HEAD':
                curve_name = current[4]
                curve_points = []
                for point in self.curves[curve_name]:
                    x = HydParam.Flow.to_si(inp_units, point[0])
                    y = HydParam.HydraulicHead.to_si(inp_units, point[1])
                    curve_points.append((x, y))
                wn.add_curve(curve_name, 'HEAD', curve_points)
                curve = wn.get_curve(curve_name)
                wn.add_pump(current[0],
                            current[1],
                            current[2],
                            'HEAD',
                            curve)
            elif current[3].upper() == 'POWER':
                wn.add_pump(current[0],
                            current[1],
                            current[2],
                            current[3].upper(),
                            HydParam.Power.to_si(inp_units, float(current[4])))
            else:
                logger.error('%(fname)s:%(lnum)-6d %(sec)13s pump keyword not recognized: "%(line)s"', edata)
                raise RuntimeError('Pump keyword in inp file not recognized.')

        for lnum, line in self.sections['[VALVES]']:
            edata['lnum'] = lnum
            edata['sec'] = '[VALVES]'
            edata['line'] = line
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if len(current) < 7:
                current[6] = 0
            valve_type = current[4].upper()
            if valve_type != 'PRV':
                logger.warning("%(fname)s:%(lnum)-6d %(sec)13s only PRV valves are currently supported.", edata)
            if float(current[6]) != 0:
                logger.warning('%(fname)s:%(lnum)-6d %(sec)13s currently, only the EpanetSimulator supports non-zero minor losses in valves.', edata)
            if valve_type in ['PRV', 'PSV', 'PBV']:
                valve_set = HydParam.Pressure.to_si(inp_units, float(current[5]))
            elif valve_type == 'FCV':
                valve_set = HydParam.Flow.to_si(inp_units, float(current[5]))
            elif valve_type == 'TCV':
                valve_set = float(current[5])
            elif valve_type == 'GPV':
                valve_set = current[5]
            else:
                logger.error('%(fname)s:%(lnum)-6d %(sec)13s valve type unrecognized: %(line)s', edata)
                raise RuntimeError('VALVE type "%s" unrecognized' % valve_type)
            wn.add_valve(current[0],
                         current[1],
                         current[2],
                         HydParam.PipeDiameter.to_si(inp_units, float(current[3])),
                         current[4].upper(),
                         float(current[6]),
                         valve_set)

        for lnum, line in self.sections['[COORDINATES]']:
            edata['lnum'] = lnum
            edata['sec'] = '[COORDINATES]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            assert(len(current) == 3), ("Error reading node coordinates. Check format.")
            wn.set_node_coordinates(current[0], (float(current[1]), float(current[2])))

        time_format = ['am', 'AM', 'pm', 'PM']
        for lnum, line in self.sections['[TIMES]']:
            edata['lnum'] = lnum
            edata['sec'] = '[TIMES]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            if (current[0].upper() == 'DURATION'):
                opts.duration = _str_time_to_sec(current[1])
            elif (current[0].upper() == 'HYDRAULIC'):
                opts.hydraulic_timestep = _str_time_to_sec(current[2])
            elif (current[0].upper() == 'QUALITY'):
                opts.quality_timestep = _str_time_to_sec(current[2])
            elif (current[1].upper() == 'CLOCKTIME'):
                [time, time_format] = [current[2], current[3].upper()]
                opts.start_clocktime = _clock_time_to_sec(time, time_format)
            elif (current[0].upper() == 'STATISTIC'):
                opts.statistic = current[1].upper()
            else:  # Other time options
                key_string = current[0] + '_' + current[1]
                setattr(opts, key_string.lower(), _str_time_to_sec(current[2]))

        if opts.pattern_start != 0.0:
            logger.warning('Currently, only the EpanetSimulator supports a non-zero patern start time.')

        if opts.report_start != 0.0:
            logger.warning('Currently, only the EpanetSimulator supports a non-zero report start time.')

        if opts.report_timestep != opts.hydraulic_timestep:
            logger.warning('Currently, only a the EpanetSimulator supports a report timestep that is not equal to the hydraulic timestep.')

        if opts.start_clocktime != 0.0:
            logger.warning('Currently, only the EpanetSimulator supports a start clocktime other than 12 am.')

        if opts.statistic != 'NONE':
            logger.warning('Currently, only the EpanetSimulator supports the STATISTIC option in the inp file.')

        for lnum, line in self.sections['[STATUS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[STATUS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            assert(len(current) == 2), ("Error reading [STATUS] block, Check format.")
            link = wn.get_link(current[0])
            if (current[1].upper() == 'OPEN' or
                    current[1].upper() == 'CLOSED' or
                    current[1].upper() == 'ACTIVE'):
                new_status = LinkBaseStatus[current[1].upper()].value
                link.status = new_status
                link._base_status = new_status
            else:
                if isinstance(link, wntr.network.Pump):
                    logger.warning('Currently, pump speed settings are only supported in the EpanetSimulator.')
                    continue
                elif isinstance(link, wntr.network.Valve):
                    if link.valve_type != 'PRV':
                        logger.warning('Currently, valves of type ' + link.valve_type + ' are only supported in the EpanetSimulator.')
                        continue
                    else:
                        setting = HydParam.Pressure.to_si(inp_units, float(current[2]))
                        link.setting = setting
                        link._base_setting = setting

        for lnum, line in self.sections['[CONTROLS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[CONTROLS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            current_copy = current
            current = [i.upper() for i in current]
            current[1] = current_copy[1]  # don't capitalize the link name

            # Create the control action object
            link_name = current[1]

            # print (link_name in wn._links.keys())
            link = wn.get_link(link_name)
            if type(current[2]) == float or type(current[2]) == int:
                if isinstance(link, wntr.network.Pump):
                    logger.warning('Currently, pump speed settings are only supported in the EpanetSimulator.')
                    continue
                elif isinstance(link, wntr.network.Valve):
                    if link.valve_type != 'PRV':
                        logger.warning('Currently, valves of type %s are only supported in the EpanetSimulator.',link.valve_type)
                        continue
                    else:
                        status = HydParam.Pressure.to_si(inp_units,
                                                         float(current[2]))
                        action_obj = wntr.network.ControlAction(link, 'setting', status)
            else:
                status = LinkBaseStatus[current[2].upper()].value
                action_obj = wntr.network.ControlAction(link, 'status', status)

            # Create the control object
            if 'TIME' not in current and 'CLOCKTIME' not in current:
                current[5] = current_copy[5]
                if 'IF' in current:
                    node_name = current[5]
                    node = wn.get_node(node_name)
                    if current[6] == 'ABOVE':
                        oper = np.greater
                    elif current[6] == 'BELOW':
                        oper = np.less
                    else:
                        raise RuntimeError("The following control is not recognized: " + line)
                    # OKAY - we are adding in the elevation. This is A PROBLEM
                    # IN THE INP WRITER. Now that we know, we can fix it, but
                    # if this changes, it will affect multiple pieces, just an
                    # FYI.
                    if isinstance(node, wntr.network.Junction):
                        threshold = HydParam.Pressure.to_si(inp_units, float(current[7])) + node.elevation
                    elif isinstance(node, wntr.network.Tank):
                        threshold = HydParam.Length.to_si(inp_units,  float(current[7])) + node.elevation
                    control_obj = wntr.network.ConditionalControl((node, 'head'), oper, threshold, action_obj)
                else:
                    raise RuntimeError("The following control is not recognized: " + line)
                control_name = ''
                for i in range(len(current)-1):
                    control_name = control_name + current[i]
                control_name = control_name + str(round(threshold, 2))
            else:
                if len(current) != 6:
                    logger.warning('Using CLOCKTIME in time controls is currently only supported by the EpanetSimulator.')
                if len(current) == 6:  # at time
                    if ':' in current[5]:
                        fire_time = int(_str_time_to_sec(current[5]))
                    else:
                        fire_time = int(float(current[5])*3600)
                    control_obj = wntr.network.TimeControl(wn, fire_time, 'SIM_TIME', False, action_obj)
                    control_name = ''
                    for i in range(len(current)-1):
                        control_name = control_name + current[i]
                    control_name = control_name + str(fire_time)
                elif len(current) == 7:  # at clocktime
                    fire_time = int(_clock_time_to_sec(current[5], current[6]))
                    control_obj = wntr.network.TimeControl(wn, fire_time, 'SHIFTED_TIME', True, action_obj)
            wn.add_control(control_name, control_obj)

        BulkReactionCoeff = QualParam.BulkReactionCoeff
        WallReactionCoeff = QualParam.WallReactionCoeff
        for lnum, line in self.sections['[REACTIONS]']:
            edata['lnum'] = lnum
            edata['sec'] = '[REACTIONS]'
            line = line.split(';')[0]
            current = line.split()
            if current == []:
                continue
            assert len(current) == 3, ('INP file option in [REACTIONS] block '
                                       'not recognized: ' + line)
            key1 = current[0].upper()
            key2 = current[1].upper()
            val3 = float(current[2])
            if key1 == 'ORDER':
                if key2 == 'BULK':
                    opts.bulk_rxn_order = int(float(current[2]))
                elif key2 == 'WALL':
                    opts.wall_rxn_order = int(float(current[2]))
                elif key2 == 'TANK':
                    opts.tank_rxn_order = int(float(current[2]))
            elif key1 == 'GLOBAL':
                if key2 == 'BULK':
                    opts.bulk_rxn_coeff = BulkReactionCoeff.to_si(inp_units, val3, mass_units, opts.bulk_rxn_order)
                elif key2 == 'WALL':
                    opts.wall_rxn_coeff = WallReactionCoeff.to_si(inp_units, val3, mass_units, opts.wall_rxn_order)
            elif key1 == 'BULK':
                pipe = wn.get_link(current[1])
                pipe.bulk_rxn_coeff = BulkReactionCoeff.to_si(inp_units, val3, mass_units, opts.bulk_rxn_order)
            elif key1 == 'WALL':
                pipe = wn.get_link(current[1])
                pipe.wall_rxn_coeff = WallReactionCoeff.to_si(inp_units, val3, mass_units, opts.wall_rxn_order)
            elif key1 == 'TANK':
                tank = wn.get_node(current[1])
                tank.bulk_rxn_coeff = BulkReactionCoeff.to_si(inp_units, val3, mass_units, opts.bulk_rxn_order)
            elif key1 == 'LIMITING':
                opts.limiting_potential = float(current[2])
            elif key1 == 'ROUGHNESS':
                opts.roughness_correlation = float(current[2])
            else:
                raise RuntimeError('Reaction option not recognized')

        if len(self.sections['[TITLE]']) > 0:
            pass
            # wn._en_title = '\n'.join(self.sections['[TITLE]'])
        else:
            pass

        if len(self.sections['[ENERGY]']) > 0:
            # wn._en_energy = '\n'.join(self.sections['[ENERGY]'])
            logger.warning('ENERGY section is reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[RULES]']) > 0:
            # wn._en_rules = '\n'.join(self.sections['[RULES]'])
            logger.warning('RULES are reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[DEMANDS]']) > 0:
            # wn._en_demands = '\n'.join(self.sections['[DEMANDS]'])
            logger.warning('Multiple DEMANDS are reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[QUALITY]']) > 0:
            # wn._en_quality = '\n'.join(self.sections['[QUALITY]'])
            logger.warning('QUALITY section is reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[EMITTERS]']) > 0:
            # wn._en_emitters = '\n'.join(self.sections['[EMITTERS]'])
            logger.warning('EMITTERS are currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[SOURCES]']) > 0:
            logger.warning('SOURCES are currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[MIXING]']) > 0:
            logger.warning('MIXING is currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[REPORT]']) > 0:
            logger.warning('REPORT is currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[VERTICES]']) > 0:
            logger.warning('VERTICES are currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[LABELS]']) > 0:
            logger.warning('LABELS are currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[BACKDROP]']) > 0:
            logger.warning('BACKDROP is currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        if len(self.sections['[TAGS]']) > 0:
            logger.warning('TAGS are currently reapplied directly to an Epanet INP file on write; otherwise unsupported.')

        # Set the _inpfile io data inside the water network, so it is saved somewhere
        wn._inpfile = self
        return wn

    def write(self, filename, wn, units=None):
        """Write the current network into an EPANET inp file.

        Parameters
        ----------
        filename : str
            Name of the inp file. example - Net3_adjusted_demands.inp
        units : str, int or FlowUnits
            Name of the units being written to the inp file.

        """

        if units is not None and isinstance(units, str):
            units=units.upper()
            inp_units = FlowUnits[units]
        elif units is not None and isinstance(units, FlowUnits):
            inp_units = units
        elif units is not None and isinstance(units, int):
            inp_units = FlowUnits(units)
        elif self.flow_units is not None:
            inp_units = self.flow_units
        else:
            inp_units = FlowUnits.GPM
        if self.mass_units is not None:
            mass_units = self.mass_units
        else:
            mass_units = MassUnits.mg

        f = io.open(filename, 'wb')

        # Print title
        if wn.name is not None:
            f.write('; Filename: {0}\n'.format(wn.name).encode('ascii'))
            f.write('; WNTR: {}\n; Created: {:%Y-%m-%d %H:%M:%S}\n'.format(wntr.__version__, datetime.datetime.now()).encode('ascii'))
        f.write('[TITLE]\n'.encode('ascii'))
        for lnum, line in self.sections['[TITLE]']:
            f.write('{}\n'.format(line).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print junctions information
        f.write('[JUNCTIONS]\n'.encode('ascii'))
        f.write(_JUNC_LABEL.format(';ID', 'Elevation', 'Demand', 'Pattern').encode('ascii'))
        nnames = list(wn._junctions.keys())
        nnames.sort()
        for junction_name in nnames:
            junction = wn._junctions[junction_name]
            E = {'name': junction_name,
                 'elev': HydParam.Elevation.from_si(inp_units, junction.elevation),
                 'dem': HydParam.Demand.from_si(inp_units, junction.base_demand),
                 'pat': '',
                 'com': ';'}
            if junction.demand_pattern_name is not None:
                E['pat'] = junction.demand_pattern_name
            f.write(_JUNC_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print reservoir information
        f.write('[RESERVOIRS]\n'.encode('ascii'))
        f.write(_RES_LABEL.format(';ID', 'Head', 'Pattern').encode('ascii'))
        nnames = list(wn._reservoirs.keys())
        nnames.sort()
        for reservoir_name in nnames:
            reservoir = wn._reservoirs[reservoir_name]
            E = {'name': reservoir_name,
                 'head': HydParam.HydraulicHead.from_si(inp_units, reservoir.base_head),
                 'com': ';'}
            if reservoir.head_pattern_name is None:
                E['pat'] = ''
            else:
                E['pat'] = reservoir.head_pattern_name
            f.write(_RES_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print tank information
        f.write('[TANKS]\n'.encode('ascii'))
        f.write(_TANK_LABEL.format(';ID', 'Elevation', 'Init Level', 'Min Level', 'Max Level',
                                   'Diameter', 'Min Volume', 'Volume Curve').encode('ascii'))
        nnames = list(wn._tanks.keys())
        nnames.sort()
        for tank_name in nnames:
            tank = wn._tanks[tank_name]
            E = {'name': tank_name,
                 'elev': HydParam.Elevation.from_si(inp_units, tank.elevation),
                 'initlev': HydParam.HydraulicHead.from_si(inp_units, tank.init_level),
                 'minlev': HydParam.HydraulicHead.from_si(inp_units, tank.min_level),
                 'maxlev': HydParam.HydraulicHead.from_si(inp_units, tank.max_level),
                 'diam': HydParam.TankDiameter.from_si(inp_units, tank.diameter),
                 'minvol': HydParam.Volume.from_si(inp_units, tank.min_vol),
                 'curve': '',
                 'com': ';'}
            if tank.vol_curve is not None:
                E['curve'] = tank.vol_curve
            f.write(_TANK_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print pipe information
        f.write('[PIPES]\n'.encode('ascii'))
        f.write(_PIPE_LABEL.format(';ID', 'Node1', 'Node2', 'Length', 'Diameter',
                                   'Roughness', 'Minor Loss', 'Status').encode('ascii'))
        lnames = list(wn._pipes.keys())
        lnames.sort()
        for pipe_name in lnames:
            pipe = wn._pipes[pipe_name]
            E = {'name': pipe_name,
                 'node1': pipe.start_node(),
                 'node2': pipe.end_node(),
                 'len': HydParam.Length.from_si(inp_units, pipe.length),
                 'diam': HydParam.PipeDiameter.from_si(inp_units, pipe.diameter),
                 'rough': pipe.roughness,
                 'mloss': pipe.minor_loss,
                 'status': LinkBaseStatus(pipe.get_base_status()).name,
                 'com': ';'}
            if pipe.cv:
                E['status'] = 'CV'
            f.write(_PIPE_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print pump information
        f.write('[PUMPS]\n'.encode('ascii'))
        f.write(_PUMP_LABEL.format(';ID', 'Node1', 'Node2', 'Parameters').encode('ascii'))
        lnames = list(wn._pumps.keys())
        lnames.sort()
        for pump_name in lnames:
            pump = wn._pumps[pump_name]
            E = {'name': pump_name,
                 'node1': pump.start_node(),
                 'node2': pump.end_node(),
                 'ptype': pump.info_type,
                 'params': '',
                 'com': ';'}
            if pump.info_type == 'HEAD':
                E['params'] = pump.curve.name
            elif pump.info_type == 'POWER':
                E['params'] = str(HydParam.Power.from_si(inp_units, pump.power))
            else:
                raise RuntimeError('Only head or power info is supported of pumps.')
            f.write(_PUMP_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print valve information
        f.write('[VALVES]\n'.encode('ascii'))
        f.write(_VALVE_LABEL.format(';ID', 'Node1', 'Node2', 'Diameter', 'Type', 'Setting', 'Minor Loss').encode('ascii'))
        lnames = list(wn._valves.keys())
        lnames.sort()
        for valve_name in lnames:
            valve = wn._valves[valve_name]
            E = {'name': valve_name,
                 'node1': valve.start_node(),
                 'node2': valve.end_node(),
                 'diam': HydParam.PipeDiameter.from_si(inp_units, valve.diameter),
                 'vtype': valve.valve_type,
                 'set': valve._base_setting,
                 'mloss': valve.minor_loss,
                 'com': ';'}
            f.write(_VALVE_ENTRY.format(**E).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print status information
        f.write('[STATUS]\n'.encode('ascii'))
        f.write( '{:10s} {:10s}\n'.format(';ID', 'Setting').encode('ascii'))
        for link_name, link in wn.links(Pump):
            if link.get_base_status() == LinkBaseStatus.CLOSED.value:
                f.write('{:10s} {:10s}\n'.format(link_name,
                        LinkBaseStatus(link.get_base_status()).name).encode('ascii'))
        for link_name, link in wn.links(Valve):
            if link.get_base_status() == LinkBaseStatus.CLOSED.value or link.get_base_status() == LinkBaseStatus.OPEN.value:
                f.write('{:10s} {:10s}\n'.format(link_name,
                        LinkBaseStatus(link.get_base_status()).name).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print pattern information
        num_columns = 8
        f.write('[PATTERNS]\n'.encode('ascii'))
        f.write('{:10s} {:10s}\n'.format(';ID', 'Multipliers').encode('ascii'))
        for pattern_name, pattern in wn._patterns.items():
            count = 0
            for i in pattern:
                if count % num_columns == 0:
                    f.write('\n{:s} {:f}'.format(pattern_name, i).encode('ascii'))
                else:
                    f.write(' {:f}'.format(i).encode('ascii'))
                count += 1
            f.write('\n'.encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print curves
        f.write('[CURVES]\n'.encode('ascii'))
        f.write(_CURVE_LABEL.format(';ID', 'X-Value', 'Y-Value').encode('ascii'))
        for curve_name, curve in wn._curves.items():
            if curve.curve_type == 'VOLUME':
                f.write(';VOLUME: {}\n'.format(curve_name).encode('ascii'))
                for point in curve.points:
                    x = HydParam.Length.from_si(inp_units, point[0])
                    y = HydParam.Volume.from_si(inp_units, point[1])
                    f.write(_CURVE_ENTRY.format(name=curve_name, x=x, y=y, com=';').encode('ascii'))
            elif curve.curve_type == 'HEAD':
                f.write(';HEAD: {}\n'.format(curve_name).encode('ascii'))
                for point in curve.points:
                    x = HydParam.Flow.from_si(inp_units, point[0])
                    y = HydParam.HydraulicHead.from_si(inp_units, point[1])
                    f.write(_CURVE_ENTRY.format(name=curve_name, x=x, y=y, com=';').encode('ascii'))
            f.write('\n'.encode('ascii'))
        for curve_name, curve in self.curves.items():
            if curve_name not in wn._curves.keys():
                for point in curve:
                    f.write(_CURVE_ENTRY.format(name=curve_name, x=point[0], y=point[1], com=';').encode('ascii'))
                f.write('\n'.encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Print Controls
        f.write( '[CONTROLS]\n'.encode('ascii'))
        # Time controls and conditional controls only
        for text, all_control in wn._control_dict.items():
            if isinstance(all_control,wntr.network.TimeControl):
                entry = 'Link {link} {setting} AT {compare} {time:g}\n'
                vals = {'link': all_control._control_action._target_obj_ref.name(),
                        'setting': 'OPEN',
                        'compare': 'TIME',
                        'time': int(all_control._fire_time / 3600.0)}
                if all_control._control_action._attribute.lower() == 'status':
                    vals['setting'] = LinkBaseStatus(all_control._control_action._value).name
                else:
                    vals['setting'] = str(float(all_control._control_action._value))
                if all_control._daily_flag:
                    vals['compare'] = 'CLOCKTIME'
                f.write(entry.format(**vals).encode('ascii'))
            elif isinstance(all_control,wntr.network.ConditionalControl):
                entry = 'Link {link} {setting} IF Node {node} {compare} {thresh}\n'
                vals = {'link': all_control._control_action._target_obj_ref.name(),
                        'setting': 'OPEN',
                        'node': all_control._source_obj.name(),
                        'compare': 'above',
                        'thresh': 0.0}
                if all_control._control_action._attribute.lower() == 'status':
                    vals['setting'] = LinkBaseStatus(all_control._control_action._value).name
                else:
                    vals['setting'] = str(float(all_control._control_action._value))
                if all_control._operation is np.less:
                    vals['compare'] = 'below'
                threshold = all_control._threshold - all_control._source_obj.elevation
                vals['thresh'] = HydParam.HydraulicHead.from_si(inp_units, threshold)
                f.write(entry.format(**vals).encode('ascii'))
            else:
                raise RuntimeError('Unknown control for EPANET INP files: %s' % type(all_control))
        f.write('\n'.encode('ascii'))

        # Report
        f.write('[REPORT]\n'.encode('ascii'))
        if len(self.sections['[REPORT]']) > 0:
            for lnum, line in self.sections['[REPORT]']:
                f.write('{}\n'.format(line).encode('ascii'))
        else:
            f.write('Status Yes\n'.encode('ascii'))
            f.write('Summary yes\n'.encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Options
        f.write('[OPTIONS]\n'.encode('ascii'))
        entry_string = '{:20s} {:20s}\n'
        entry_float = '{:20s} {:g}\n'
        f.write(entry_string.format('UNITS', inp_units.name).encode('ascii'))
        f.write(entry_string.format('HEADLOSS', wn.options.headloss).encode('ascii'))
        if wn.options.hydraulics_option is not None:
            f.write('{:20s} {:s} {:<30s}\n'.format('HYDRAULICS', wn.options.hydraulics_option, wn.options.hydraulics_filename).encode('ascii'))
        if wn.options.quality_value is None:
            f.write(entry_string.format('QUALITY', wn.options.quality_option).encode('ascii'))
        else:
            f.write('{:20s} {} {}\n'.format('QUALITY', wn.options.quality_option, wn.options.quality_value).encode('ascii'))
        f.write(entry_float.format('VISCOSITY', wn.options.viscosity).encode('ascii'))
        f.write(entry_float.format('DIFFUSIVITY', wn.options.diffusivity).encode('ascii'))
        f.write(entry_float.format('SPECIFIC GRAVITY', wn.options.specific_gravity).encode('ascii'))
        f.write(entry_float.format('TRIALS', wn.options.trials).encode('ascii'))
        f.write(entry_float.format('ACCURACY', wn.options.accuracy).encode('ascii'))
        f.write(entry_float.format('CHECKFREQ', wn.options.checkfreq).encode('ascii'))
        if wn.options.unbalanced_value is None:
            f.write(entry_string.format('UNBALANCED', wn.options.unbalanced_option).encode('ascii'))
        else:
            f.write('{:20s} {:s} {:d}\n'.format('UNBALANCED', wn.options.unbalanced_option, wn.options.unbalanced_value).encode('ascii'))
        if wn.options.pattern is not None:
            f.write(entry_string.format('PATTERN', wn.options.pattern).encode('ascii'))
        f.write(entry_float.format('DEMAND MULTIPLIER', wn.options.demand_multiplier).encode('ascii'))
        f.write(entry_float.format('EMITTER EXPONENT', wn.options.emitter_exponent).encode('ascii'))
        f.write(entry_float.format('TOLERANCE', wn.options.tolerance).encode('ascii'))
        if wn.options.map is not None:
            f.write(entry_string.format('MAP', wn.options.map).encode('ascii'))

        f.write('\n'.encode('ascii'))

        # Reaction Options
        f.write( '[REACTIONS]\n'.encode('ascii'))
        entry_int = ' {:s} {:s} {:d}\n'
        entry_float = ' {:s} {:s} {:<10.4f}\n'
        f.write(entry_int.format('ORDER', 'BULK', int(wn.options.bulk_rxn_order)).encode('ascii'))
        f.write(entry_int.format('ORDER', 'WALL', int(wn.options.wall_rxn_order)).encode('ascii'))
        f.write(entry_int.format('ORDER', 'TANK', int(wn.options.tank_rxn_order)).encode('ascii'))
        f.write(entry_float.format('GLOBAL','BULK',
                                   QualParam.BulkReactionCoeff.from_si(inp_units,
                                                                       wn.options.bulk_rxn_coeff,
                                                                       mass_units,
                                                                       wn.options.bulk_rxn_order)).encode('ascii'))
        f.write(entry_float.format('GLOBAL','WALL',
                                   QualParam.WallReactionCoeff.from_si(inp_units,
                                                                       wn.options.wall_rxn_coeff,
                                                                       mass_units,
                                                                       wn.options.wall_rxn_order)).encode('ascii'))
        if wn.options.limiting_potential is not None:
            f.write(entry_float.format('LIMITING','POTENTIAL',wn.options.limiting_potential).encode('ascii'))
        if wn.options.roughness_correlation is not None:
            f.write(entry_float.format('ROUGHNESS','CORRELATION',wn.options.roughness_correlation).encode('ascii'))
        for tank_name, tank in wn.nodes(Tank):
            if tank.bulk_rxn_coeff is not None:
                f.write(entry_float.format('TANK',tank_name,
                                           QualParam.BulkReactionCoeff.from_si(inp_units,
                                                                       tank.bulk_rxn_coeff,
                                                                       mass_units,
                                                                       wn.options.bulk_rxn_order)).encode('ascii'))
        for pipe_name, pipe in wn.links(Pipe):
            if pipe.bulk_rxn_coeff is not None:
                f.write(entry_float.format('BULK',pipe_name,
                                           QualParam.BulkReactionCoeff.from_si(inp_units,
                                                                       pipe.bulk_rxn_coeff,
                                                                       mass_units,
                                                                       wn.options.bulk_rxn_order)).encode('ascii'))
            if pipe.wall_rxn_coeff is not None:
                f.write(entry_float.format('WALL',pipe_name,
                                           QualParam.WallReactionCoeff.from_si(inp_units,
                                                                       pipe.wall_rxn_coeff,
                                                                       mass_units,
                                                                       wn.options.wall_rxn_order)).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Time options
        f.write('[TIMES]\n'.encode('ascii'))
        entry = '{:20s} {:10s}\n'
        time_entry = '{:20s} {:02d}:{:02d}:{:02d}\n'
        hrs, mm, sec = _sec_to_string(wn.options.duration)
        f.write(time_entry.format('DURATION', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.hydraulic_timestep)
        f.write(time_entry.format('HYDRAULIC TIMESTEP', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.pattern_timestep)
        f.write(time_entry.format('PATTERN TIMESTEP', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.pattern_start)
        f.write(time_entry.format('PATTERN START', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.report_timestep)
        f.write(time_entry.format('REPORT TIMESTEP', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.report_start)
        f.write(time_entry.format('REPORT START', hrs, mm, sec).encode('ascii'))

        hrs, mm, sec = _sec_to_string(wn.options.start_clocktime)
        if hrs < 12:
            time_format = ' AM'
        else:
            hrs -= 12
            time_format = ' PM'
        f.write('{:20s} {:02d}:{:02d}:{:02d}{:s}\n'.format('START CLOCKTIME', hrs, mm, sec, time_format).encode('ascii'))

        hrs, mm, sec = _sec_to_string(wn.options.quality_timestep)
        f.write(time_entry.format('QUALITY TIMESTEP', hrs, mm, sec).encode('ascii'))
        hrs, mm, sec = _sec_to_string(wn.options.rule_timestep)
        f.write(time_entry.format('RULE TIMESTEP', hrs, mm, int(sec)).encode('ascii'))
        f.write(entry.format('STATISTIC', wn.options.statistic).encode('ascii'))
        f.write('\n'.encode('ascii'))

        # Coordinates
        f.write('[COORDINATES]\n'.encode('ascii'))
        entry = '{:10s} {:10g} {:10g}\n'
        label = '{:10s} {:10s} {:10s}\n'
        f.write(label.format(';Node', 'X-Coord', 'Y-Coord').encode('ascii'))
        coord = nx.get_node_attributes(wn._graph, 'pos')
        for key, val in coord.items():
            f.write(entry.format(key, val[0], val[1]).encode('ascii'))
        f.write('\n'.encode('ascii'))

        unmodified = ['[ENERGY]', '[RULES]', '[DEMANDS]', '[QUALITY]', '[EMITTERS]', '[SOURCES]',
                      '[MIXING]', '[VERTICES]', '[LABELS]', '[BACKDROP]', '[TAGS]']

        for section in unmodified:
            if len(self.sections[section]) > 0:
                logger.debug('Writting data from original epanet file: %s', section)
                f.write('{0}\n'.format(section).encode('ascii'))
                for lnum, line in self.sections[section]:
                    f.write('{0}\n'.format(line).encode('ascii'))
                f.write('\n'.encode('ascii'))

        f.write('[END]\n'.encode('ascii'))
        f.close()


#class HydFile(object):
#    """An EPANET hydraulics file (binary) reader/writer."""
#    pass
#
#
#class BinFile(object):
#    """An EPANET ressults file (binray) reader."""
#    pass
#
#
#class RptFile(object):
#    """An EPANET report file (text) reader."""
#    pass
#