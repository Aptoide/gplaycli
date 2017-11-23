#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPlay-Cli
Copyleft (C) 2015 Matlink
Hardly based on GooglePlayDownloader https://framagit.org/tuxicoman/googleplaydownloader
Copyright (C) 2013 Tuxicoman

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version.
This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more
details.
You should have received a copy of the GNU Affero General Public License along with this program.  If not,
see <http://www.gnu.org/licenses/>.
"""

import sys
import os
import logging
import argparse
import requests
import configparser

from enum import IntEnum
from gpapi.googleplay import GooglePlayAPI
from gpapi.googleplay import LoginError
from pkg_resources import get_distribution, DistributionNotFound
from google.protobuf.message import DecodeError as GoogleDecodeError

from . import util

try:
    import keyring
    HAVE_KEYRING = True
except ImportError:
    HAVE_KEYRING = False


try:
    __version__ = '%s [Python%s] ' % (get_distribution('gplaycli').version, sys.version.split()[0])
except DistributionNotFound:
    __version__ = 'unknown: gplaycli not installed (version in setup.py)'


class ERRORS(IntEnum):
    OK = 0
    TOKEN_DISPENSER_AUTH_ERROR = 5
    TOKEN_DISPENSER_SERVER_ERROR = 6
    KEYRING_NOT_INSTALLED = 10
    CANNOT_LOGIN_GPLAY = 15


class GPlaycli(object):
    def __init__(self, credentials=None, proxies=None):
        # no config file given, look for one
        if credentials is None:
            # default local user configs
            cred_paths_list = [
                'gplaycli.conf',
                os.path.expanduser("~") + '/.config/gplaycli/gplaycli.conf',
                '/etc/gplaycli/gplaycli.conf'
            ]
            tmp_list = list(cred_paths_list)
            while not os.path.isfile(tmp_list[0]):
                tmp_list.pop(0)
                if not tmp_list:
                    raise OSError("No configuration file found at %s" % cred_paths_list)
            credentials = tmp_list[0]

        self.proxies = None

        default_values = dict()
        self.configparser = configparser.ConfigParser(default_values)
        self.configparser.read(credentials)
        self.config = {key: value for key, value in self.configparser.items("Credentials")}

        self.tokencachefile = os.path.expanduser(self.configparser.get("Cache", "token"))
        self.playstore_api = None

        self.token_enable = True
        self.token_url = self.configparser.get('Credentials', 'token_url')
        self.token, self.gsfid = self.retrieve_token()

        # default settings, ie for API calls
        self.yes = False
        self.verbose = False
        logging.basicConfig()
        self.progress_bar = False
        self.logging_enable = False
        self.device_codename = 'bacon'
        self.addfiles_enable = False

    def get_cached_token(self):
        try:
            with open(self.tokencachefile, 'r') as tcf:
                token, gsfid = tcf.readline().split()
                if not token:
                    token = None
                    gsfid = None
        except (IOError, ValueError):  # cache file does not exists or is corrupted
            token = None
            gsfid = None
        return token, gsfid

    def write_cached_token(self, token, gsfid):
        try:
            # creates cachedir if not exists
            cachedir = os.path.dirname(self.tokencachefile)
            if not os.path.exists(cachedir):
                os.mkdir(cachedir)
            with open(self.tokencachefile, 'w') as tcf:
                tcf.write("%s %s" % (token, gsfid))
        except IOError as error:
            raise IOError("Failed to write token to cache file: %s %s" % (self.tokencachefile,
                                                                          error.strerror))

    def retrieve_token(self, force_new=False):
        token, gsfid = self.get_cached_token()
        if token is not None and not force_new:
            logging.info("Using cached token.")
            return token, gsfid
        logging.info("Retrieving token ...")
        resp = requests.get(self.token_url, proxies=self.proxies)
        if resp.text == 'Auth error':
            print('Token dispenser auth error, probably too many connections')
            sys.exit(ERRORS.TOKEN_DISPENSER_AUTH_ERROR)
        elif resp.text == "Server error":
            print('Token dispenser server error')
            sys.exit(ERRORS.TOKEN_DISPENSER_SERVER_ERROR)
        token, gsfid = resp.text.split(" ")
        self.token = token
        self.gsfid = gsfid
        self.write_cached_token(token, gsfid)
        return token, gsfid

    def set_download_folder(self, folder):
        self.config["download_folder_path"] = folder

    def connect_to_googleplay_api(self):
        self.playstore_api = GooglePlayAPI(device_codename=self.device_codename,
                                           proxies=self.proxies)
        error = None
        email = None
        password = None
        auth_sub_token = None
        gsf_id = None
        if self.token_enable is False:
            logging.info("Using credentials to connect to API")
            email = self.config["gmail_address"]
            if self.config["gmail_password"]:
                logging.info("Using plaintext password")
                password = self.config["gmail_password"]
            elif self.config["keyring_service"] and HAVE_KEYRING is True:
                password = keyring.get_password(self.config["keyring_service"], email)
            elif self.config["keyring_service"] and HAVE_KEYRING is False:
                print("You asked for keyring service but keyring package is not installed")
                sys.exit(ERRORS.KEYRING_NOT_INSTALLED)
        else:
            logging.info("Using token to connect to API")
            auth_sub_token = self.token
            gsf_id = int(self.gsfid, 16)
        try:
            self.playstore_api.login(email=email,
                                     password=password,
                                     authSubToken=auth_sub_token,
                                     gsfId=gsf_id)
        except (ValueError, IndexError, LoginError, GoogleDecodeError):  # invalid token or expired
            logging.info("Token has expired or is invalid. Retrieving a new one...")
            self.retrieve_token(force_new=True)
            self.playstore_api.login(authSubToken=self.token, gsfId=int(self.gsfid, 16))
        success = True
        return success, error

    def download_pkg(self, pkg, version):
        # Check for download folder
        download_folder_path = self.config["download_folder_path"]
        if not os.path.isdir(download_folder_path):
            os.mkdir(download_folder_path)

        #Download
        try:
            data_dict = self.playstore_api.download(pkg, version)
        except IndexError as exc:
            print("Error while downloading %s : %s" % (pkg,
                                                       "this package does not exist, "
                                                       "try to search it via --search before"))
            return False, None
        except LoginError:
            self.retrieve_token(force_new=True)
            self.playstore_api.login(authSubToken=self.token, gsfId=int(self.gsfid, 16))
            try:
                data_dict = self.playstore_api.download(pkg, version)
            except IndexError as exc:
                print("Error while downloading %s : %s" % (pkg,
                                                           "this package does not exist, "
                                                           "try to search it via --search before"))
                return False, None
            except Exception as exc:
                print("Error while downloading %s : %s" % (pkg, exc))
                return False, None
        except Exception as exc:
            print("Error while downloading %s : %s" % (pkg, exc))
            return False, None
        else:
            filename = pkg + ".apk"
            filepath = os.path.join(download_folder_path, filename)

            data = data_dict['data']
            additional_data = data_dict['additionalData']

            try:
                open(filepath, "wb").write(data)
                if additional_data:
                    for obb_file in additional_data:
                        obb_filename = "%s.%s.%s.obb" % (obb_file["type"],
                                                         obb_file["versionCode"],
                                                         data_dict["docId"])
                        obb_filename = os.path.join(download_folder_path, obb_filename)
                        open(obb_filename, "wb").write(obb_file["data"])
            except IOError as exc:
                print("Error while writing %s : %s" % (pkg, exc))
        return True, filepath

    def raw_search(self, search_string, nb_results):
        # Query results
        return self.playstore_api.search(search_string, nb_result=nb_results)

    def search(self, search_string, nb_results=1, free_only=True):
        try:
            results = self.raw_search(search_string, nb_results)
        except IndexError:
            results = list()
        except LoginError:
            self.retrieve_token(force_new=True)
            self.playstore_api.login(authSubToken=self.token, gsfId=int(self.gsfid, 16))
            try:
                results = self.raw_search(search_string, nb_results)
            except IndexError:
                results = list()

        if not results:
            print("No result")
            return
        all_results = list()
        # Compute results values
        for result in results:
            if free_only and result['offer'][0]['checkoutFlowRequired']:  # if not Free to download
                continue
            entry = {"title": result["title"],
                     "creator": result['author'],
                     "size": util.sizeof_fmt(result['installationSize']),
                     "downloads": result['numDownloads'],
                     "last_update": result['uploadDate'],
                     "app_id": result['docId'],
                     "version": result['versionCode'],
                     "rating": "%.2f" % result["aggregateRating"]["starRating"],
                     "paid": result['offer'][0]['checkoutFlowRequired']}
            all_results.append(entry)

        for result in all_results:
            if result["app_id"] == search_string:
                return result
        return "NOT_AT_PLAY"

def main():
    parser = argparse.ArgumentParser(description="A Google Play Store Apk downloader and manager for command line")
    parser.add_argument('-V', '--version', action='store_true', dest='version',
                        help='Print version number and exit')
    parser.add_argument('-s', '--search', action='store', dest='search_string', metavar="SEARCH",
                        type=str, help="Search the given string in Google Play Store")
    parser.add_argument('-P', '--paid', action='store_true', dest='paid',
                        default=False, help='Also search for paid apps')
    parser.add_argument('-n', '--number', action='store', dest='number_results',
                        metavar="NUMBER", type=int,
                        help="For the search option, returns the given number of matching applications")
    parser.add_argument('-d', '--download', action='store', dest='package_to_download',
                        metavar="AppID", type=str, help="Download the App that map given AppID")
    parser.add_argument('-a', '--additional-files', action='store_true', dest='addfiles_enable',
                        default=False, help="Enable the download of additional files")
    parser.add_argument('-f', '--folder', action='store', dest='dest_folder',
                        metavar="FOLDER", nargs=1, type=str, default=".",
                        help="Where to put the downloaded Apks, only for -d command")
    parser.add_argument('-t', '--token', action='store_true', dest='token_enable', default=None,
                        help='Instead of classical credentials, use the tokenize version')
    parser.add_argument('-tu', '--token-url', action='store', dest='token_url',
                        metavar="TOKEN_URL", type=str, default=None,
                        help="Use the given tokendispenser URL to retrieve a token")
    parser.add_argument('-c', '--config', action='store', dest='config',
                        metavar="CONF_FILE", nargs=1, type=str, default=None,
                        help="Use a different config file than gplaycli.conf")

    if len(sys.argv) < 2:
        sys.argv.append("-h")

    args = parser.parse_args()

    if args.version:
        print(__version__)
        return

    cli = GPlaycli()
    success, error = cli.connect_to_googleplay_api()
    if not success:
        logging.error("Cannot login to GooglePlay ( %s )" % error)
        sys.exit(ERRORS.CANNOT_LOGIN_GPLAY)

    if args.search_string:
        cli.verbose = True
        nb_results = 1
        if args.number_results:
            nb_results = args.number_results
        cli.search(args.search_string, nb_results, not args.paid)

    if args.package_to_download is not None:
        if args.dest_folder is not None:
            cli.set_download_folder(args.dest_folder[0])
        # cli.download_pkg(args.package_to_download)


if __name__ == '__main__':
    main()
