# -*- coding: utf-8 -*-
# @author: vuolter

import logging
import logging.handlers
import locale
import sys
import os

from contextlib import closing

try:
    import colorlog
except ImportError:
    colorlog = None
    
    
class LogFactory(object):
    def __init__(self, core):
        self.pyload = core
        self.loggers = {}
        
    def init_logger(self, name):
        logger = logging.getLogger(name)
        self.loggers[name] = logger
        return self._init_logger(logger, name)
        
    def _init_logger(self, logger, name):
        debug = self.pyload.config.get("general", "debug_mode")
        console = self.pyload.config.get('log', 'console')
        syslog = self.pyload.config.get('log', 'syslog')
        filelog = self.pyload.config.get('log', 'filelog')
        
        level = logging.DEBUG if debug else logging.INFO
        logger.setLevel(level)
        
        if console:
            self._init_console_handler(logger, name)
        if syslog:
            self._init_syslog_handler(logger, name)
        if filelog:
            self._init_filelog_handler(logger, name)
        
    def get_logger(self, name):
        return self.loggers.get(name, self.init_logger(name))
        
    def remove_logger(self, name):
        logger = self.loggers.pop(name)
        if not logger:
            return
        self._remove_handlers(logger)
        
    def reset_logger(self, name):
        logger = self.loggers.get(name)
        if not logger:
            return
        self._init_logger(logger, name)
    
    def _remove_handlers(self, logger):
        for handler in logger.handlers:
            with closing(handler) as hdlr:
                logger.removeHandler(hdlr)
                
    def shutdown(self):
        for logger in self.loggers.values():
            self._remove_handlers(logger)
        self.loggers.clear()
                             
    def _init_console_handler(self, logger, name):
        color = self.pyload.config.get('log', 'console_color') and colorlog
        
        if color:
            fmt = "{badge_log_color}[{asctime}]  {levelname:^8} {reset}{log_color} {name:>16}  {message}"
            datefmt = "%Y-%m-%d %H:%M:%S"
            
            primary_colors = {
                'WARNING': 'red,bg_yellow',
                'ERROR': 'bold,white,bg_red',
                'CRITICAL': 'bold,white,bg_black',
            }
            secondary_colors = {
                'badge': {
                    'DEBUG': 'bold,white,bg_cyan',
                    'INFO': 'bold,white,bg_green',
                    'WARNING': 'bold,white,bg_yellow',
                    'ERROR': 'bold,white,bg_red',
                    'CRITICAL': 'bold,white,bg_black',
                }
            }
            consoleform = colorlog.ColoredFormatter(
                fmt, datefmt=datefmt, log_colors=primary_colors,
                secondary_log_colors=secondary_colors, 
                style='{')
                
        else:
            fmt = "[{asctime}]  {levelname:8}  {name:>16}  {message}"
            datefmt = "%Y-%m-%d %H:%M:%S"
            consoleform = logging.Formatter(fmt, datefmt, '{')

        consolehdlr = logging.StreamHandler(sys.stdout)
        consolehdlr.setFormatter(consoleform)
        logger.addHandler(consolehdlr)
             
    def _init_syslog_handler(self, logger, name):
        # try to mimic to normal syslog messages
        fmt = '{asctime} {name}: {message}'
        datefmt = '%b %e %H:%M:%S'
        
        syslogform = logging.Formatter(fmt, datefmt, "{")
        syslogaddr = None

        location = self.pyload.config.get('log', 'syslog_location')
        if location == 'remote':
            host = self.pyload.config.get('log', 'syslog_host')
            port = self.pyload.config.get('log', 'syslog_port')
            syslogaddr = (host, port)
        else:
            folder = self.pyload.config.get('log', 'syslog_folder')
            if syslogaddr:
                syslogaddr = folder
            elif sys.platform == 'darwin':
                syslogaddr = '/var/run/syslog'
            elif os.name != 'nt':
                syslogaddr = '/dev/log'

        sysloghdlr = logging.handlers.SysLogHandler(syslogaddr)
        sysloghdlr.setFormatter(syslogform)
        logger.addHandler(sysloghdlr)

    def _init_filelog_handler(self, logger, name):
        fmt = "[{asctime}]  {levelname:8}  {name:20}  {message}"
        datefmt = "%Y-%m-%d %H:%M:%S"
        fileform = logging.Formatter(fmt, datefmt, '{')

        folder = self.pyload.config.get('log', 'filelog_folder')
        os.makedirs(folder, exist_ok=True)

        filename = "{}.log".format(name)
        filelog = os.path.join(folder, filename)
        encoding = locale.getpreferredencoding(do_setlocale=False)
        
        if self.pyload.config.get('log', 'filelog_rotate'):
            max_size = self.pyload.config.get('log', 'filelog_size') << 10
            max_entries = self.pyload.config.get('log', 'filelog_entries')
            
            filehdlr = logging.handlers.RotatingFileHandler(
                filelog, maxBytes=max_size, backupCount=max_entries,
                encoding=encoding)
                
        else:
            filehdlr = logging.FileHandler(filelog, encoding=encoding)

        filehdlr.setFormatter(fileform)
        logger.addHandler(filehdlr)
        