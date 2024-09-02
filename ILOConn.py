#!/usr/bin/python
#
#  
#  Original Author: Vladimir Gaitner
#
#  File Description: API for setting up REST Session with ILO 
#                    for check Status of the Server Components
#

import logging
import httplib
import ConfigParser
import json
import base64
import os
import sys
from urlparse import urlparse

#include path for dependent scripts
sys.path.append("/usr/scripts")
sys.path.append("/usr/scripts")

# Config file for Rest Client
rest_cli = '/usr/scripts/rest-cli.config'

class IloConnection:
    def __init__(self, name=None, port=None, protocol=None, auth_type="Basic"):
        """Initializes the class when a new instance is created.
    
        Args:
            name: The server name (or ip address) to connect to.
            port: The port number to create the socket on.
            protocol: Network protocol to use -- either 'http' or 'https'.
            auth_type: Authentication type -- either 'Basic' or 'Session'
        Returns:
            None
        """

        self.config = ConfigParser.ConfigParser()
        self.config.read(rest_cli)

        if name is None:
            name = self.config.get('ServerProperties', 'ServerName')
        if port is None:
            port = self.config.get('ServerProperties', 'ServerPort')
        if protocol is None:
            protocol = self.config.get('AccessProperties', 'Protocol')

        logging.info("Creating connection to %s://%s:%s" % (protocol, name, port))
        if protocol == "https":
            self.connection = httplib.HTTPSConnection(name)
        else:
            self.connection = httplib.HTTPConnection(name)

        username = self.config.get('AccessProperties', 'UserName')
        password = self.config.get('AccessProperties', 'Password')
        if auth_type == "Session":
            self.auth = SessionAuth(self.connection, username, password)
        elif auth_type == "Basic":
            self.auth = BasicAuth(self.connection, username, password)
        else:
            raise ValueError('Could not instantiate authentication type: %s' % auth_type)
    
    def send_message(self, resource, method="GET", body=None):
        """Sends an HTTP request over the established connection.

        Sends an HTTP request of the specified type (GET, POST, etc) and returns
        the response from the server.

        Args:
            resource: The url path for the HTTP request.
            method: HTTP request method (GET, POST, PUT, PATCH, DELETE)
            body: The body of the http request.
        Returns:
            A tuple containing the response object (httplib.HTTPResponse) and the content
            of the response. If the response content is json encoded, a dict will be returned,
            otherwise a string will be returned.
        """

        url = resource
        headers = {'X-API-Version': '1'}
        headers = self.auth.modify_header(headers)
        logging.debug("HTTP Header Data: " + json.dumps(headers))
       
        resp, content = IloConnection.__request(self.connection, url, method, headers, body)

        if resp.status == httplib.UNAUTHORIZED and isinstance(self.auth, SessionAuth ):
            username = self.config.get('AccessProperties', 'UserName')
            password = self.config.get('AccessProperties', 'Password')
            self.auth.create_session(self.connection, username, password)
            headers = self.auth.modify_header(headers)
            logging.debug("HTTP Header Data: " + json.dumps(headers))
            resp, content = IloConnection.__request(self.connection, url, method, headers, body)
            
        if resp.getheader('content-type') == 'application/json':
            content = json.loads(content)
        return resp, content

    @staticmethod
    def __request(connection, url, method, headers, body):
        logging.info("HTTP %s: %s" % (method, url))
        if body is not None:
            connection.request(method, url, headers=headers, body=json.dumps(body))
        else:
            connection.request(method, url, headers=headers)

        resp = connection.getresponse()
        data = resp.read()
        connection.close()
        return (resp, data)
    
class BasicAuth:
    auth_token = None
    def __init__(self, connection, username, password):
        connection.follow_all_redirects=True
        self.auth_token = base64.b64encode( username + ':' + password )

    def modify_header(self, header):
        header['Authorization'] = 'Basic ' + self.auth_token
        return header
   
class SessionAuth:
    auth_token = None
    Config = ConfigParser.ConfigParser()
    Config.read(rest_cli)
    def __init__(self, connection, username, password):
        self.stored_session_file = self.Config.get('ClientProperties', 'SessionFile')
        auth_token = self.restore_session()
        if auth_token is None:
            logging.debug("Retrieving new HTTP session authentication token.")
            self.create_session(connection, username, password)
        else:
            logging.debug("Reusing saved HTTP session authentication token.")
            self.auth_token = auth_token

    def restore_session(self):
        if not os.path.isfile(self.stored_session_file):
            return None
        f = open(self.stored_session_file, 'r')
        auth_token = f.readline()
        f.close()
        if len(auth_token) > 1:
            return auth_token
        else:
            return None

    def create_session(self, connection, username, password):
        url = '/rest/Sessions'
        auth_token = SessionAuth.__createSession(connection, url, username, password)
        if auth_token is None:
            logging.error("Create session failed. No Auth token.")
            return False
        else:
            logging.debug("Create session succeeded. auth_token=%s" % auth_token)
            self.auth_token = auth_token
            f = open(self.stored_session_file, 'w')
            f.write(auth_token)
            f.close()
        return True

    # Creates a session and returns the session token on success. None on failure.
    @staticmethod
    def __createSession(connection, url, username, password):
        headers = {'X-API-Version': '1',
                   'Content-Type':'application/json'}
        body={'UserName': username,
              'Password': password}
        method="POST"

        logging.info("HTTP %s: %s" % (method, url))
        connection.request(method, url, headers=headers, body=json.dumps(body))

        resp = connection.getresponse()
        content = resp.read()
        connection.close()
 
        logging.info("Received response: status(%s), reason(%s)" % (resp.status, resp.reason))
        if ( resp.status == httplib.TEMPORARY_REDIRECT ):
            redirect_location = urlparse(resp.getheader('location')).path
            return SessionAuth.__createSession(connection, redirect_location, username, password)
        elif ( resp.status == httplib.CREATED ):
            auth_token = resp.getheader('x-auth-token')
            jsondata = json.loads(content)
            json.dumps(resp.getheaders(), sort_keys=True, indent=4, separators=(',', ': '))
            json.dumps(jsondata, sort_keys=True, indent=4, separators=(',', ': '))
            return auth_token
        else:
            return None
    def modify_header(self, header):
        header['X-Auth-Token'] = self.auth_token
        return header

    def __isExpired():
        return False

def simple_self_test():
    """Makes a test connection with the server.

    Tests the REST API interface by querying two well known resource endpoints
    with each of the supported authetication types.

    Returns:
        Returns zero (0) on completion. Return value does not indicate 
        a successfull connection to the server.
    """

    logging.info("Testing Connection to iLO using Basic Authentication")
    connection = IloConnection(auth_type="Basic")
    resp, content = connection.send_message('/rest/v1/Chassis/1/ThermalMetrics')
    logging.info("Received response: status(%s), reason(%s)" % (resp.status, resp.reason))

    logging.info("Testing Connection to iLO using Session Authentication")
    connection = IloConnection(auth_type="Session")
    resp, content = connection.send_message('/rest/v1/Chassis/1/PowerMetrics')
    logging.info("Received response: status(%s), reason(%s)" % (resp.status, resp.reason))

    return 0

def query_path(path):
    """Queries the specified REST resource and prints the response.

    Makes an HTTP GET request to the specified REST resource. The response
    content is printed to stdout. 

    Args:
        path: The url path for the HTTP request.
    Returns:
        Returns zero (0) on completion. Return value does not indicate
        a successfull connection to the server.
    """

    connection = IloConnection(auth_type="Basic")
    resp, content = connection.send_message(path)
   
    logging.info("Received response: status(%s), reason(%s)" % (resp.status, resp.reason))
    if resp.status is not httplib.OK:
        logging.info("HTTP Header Data: " + json.dumps(resp.getheaders()))
       
    json_data = json.dumps(content, sort_keys=False, indent=4)
    print json_data

    return 0

def main():
   """ Main function to be invoked if this script is executed from the command-line. """

   # Dependencies that are only imported when the file is run
   # from the commandline. (i.e. not imported as a module)
   from optparse import OptionParser

   parser = OptionParser(description='Test connection with the PARC iLO (integrated Lights-Out) REST interface.')
   parser.add_option("-d", '--debug', dest='debug', action='store_true', help='Show debug log messages.')
   parser.add_option('-p', '--path', dest='path', metavar='PATH', help='A url path to query from the server. (E.g. /rest/v1)')
   parser.add_option('-t', '--test', dest='test', action='store_true', help='Runs a simple test for connectivity with the server.')

   (options, args) = parser.parse_args()
   if options.test and options.path:
       parser.error("Cannot run in test mode (--test) with the --path option.")

   if options.debug:
       logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s', level=logging.DEBUG, datefmt='%m/%d/%Y %I:%M:%S %p')
   else:
       logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)

   if options.test:
       return simple_self_test()
   elif options.path:
       return query_path(options.path)
   else:
       parser.print_help()
       return 1

# Check if script is being directly invoked (e.g. from command-line)
if __name__ == "__main__":
   rc = main()
   exit(rc)
