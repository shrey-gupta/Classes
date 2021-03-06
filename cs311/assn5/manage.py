#!/usr/bin/env python 
import socket
from xml.dom import minidom
import signal
import os
import time
import select
import sys
from math import sqrt

#Global variables
perfect_numbers = []
compute_processes = dict() #Dictionary to hold compute processes by hostname->mods_per_sec
compute_cnc_sock = []  #Tracks compute command and control (CnC) socket


#signal handler
def handler(signum, frame):
	terminate_computes()
	sys.exit()

#Handles client taking too long to respond
def timeout(signum, frame):
	print "Client took too long to respond. Exiting"
	terminate_computes()
	
# Set the signal handlers
signal.signal(signal.SIGHUP, handler)
signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGQUIT, handler)
signal.signal(signal.SIGALRM, timeout) # for timeout


# to do: bump HOST and PORT into a lib file and have report and manage pull from that
HOST = "localhost"                 # Symbolic name meaning all available interfaces
PORT = 43283              # "4DAVE"
BUFFSIZE = 4096

srvsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srvsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) #Prevent socket from being left in TIME_WAIT state
srvsock.bind((HOST, PORT))
srvsock.listen(5) #5 is the maximum number of queued connections
descriptors = [srvsock] #array of sockets

#Sends a handshake to a client
def send_handshake(sock):
	sock.send("<request type=\"handshake\" sender=\"manage\"></request>")

#Gets a handshake from a client
def get_handshake(sock):
	data = get_data(sock)
	packet = data[0]
	xmldoc = minidom.parseString(packet)
	x = xmldoc.getElementsByTagName('request')[0]
	client = x.attributes['sender'].value
	request_type = x.attributes['type'].value
	host,port = sock.getpeername()
	if(request_type == "handshake"):
		if(client == "compute"):
			print "New Compute client joined from", host,":",port
			x = xmldoc.getElementsByTagName('performance')[0]
			mods_per_sec = x.attributes['mods_per_sec'].value
			compute_processes[host] = mods_per_sec
		elif(client == "compute_cnc"):
			print "Compute CnC client joined from", host,":",port
			compute_cnc_sock.append(sock)
		elif(client == "report"):
			print "Report client joined from", host,":",port

#Finds the next maximum based on the number of mods per second the client can handle
def next_max(prev_max, mods_per_sec):
	mod_ceiling = 0.6 * (mods_per_sec * 15)
	num_mods = 0
	i = prev_max + 1
	while(num_mods < mod_ceiling):
		num_mods += sqrt(i)
		i += 1
	return i

#Returns an array of packet data split by newline
def get_data(sock):
	data = sock.recv(BUFFSIZE)
	data = data.strip() #remove trailing and leading whitespace
	data = data.split('\n')
	return data

#Sends terminate packet to Compute CnC and then exits
def terminate_computes():
	if(compute_cnc_sock):
		print "Sending terminate packet"
		(cnc_read, cnc_write, cnc_exc) = select.select([], compute_cnc_sock, [])
		cnc_write[0].send("<request type=\"terminate\" sender=\"manage\"></request>")
	sys.exit(0)

while 1:
	#reference: http://stackoverflow.com/questions/9306412/python-socket-programming-need-to-do-something-while-listening-for-connections
	#multiplex the socket. Wait for an event on a readable socket
	(sread, swrite, sexc) = select.select(descriptors, [], [])
	for sock in sread:
		if(sock == srvsock): #New connection
			newsock, (remhost, remport) = srvsock.accept()
			descriptors.append(newsock)
			send_handshake(newsock)
			get_handshake(newsock)
		else:
			data = get_data(sock)
			if(data == ''):
					print "Client left"
					sock.close()
					descriptors.remove(sock)
			else:
				for packet in data:
					if(packet != ''):
						xmldoc = minidom.parseString(packet)
						x = xmldoc.getElementsByTagName('request')[0]
						client = x.attributes['sender'].value
						request_type = x.attributes['type'].value
						if(client == "compute"):
							if(request_type == "request_range"):
								x = xmldoc.getElementsByTagName('performance')[0]
								mods_per_sec = int(x.attributes['mods_per_sec'].value)
								host,port = sock.getpeername()
								compute_processes[host] = mods_per_sec
								x = xmldoc.getElementsByTagName('prev_max')[0]
								prev_max = int(x.attributes['value'].value)
								#Make sure the client hasn't reached the upper limit on calculations it can handle
								if(prev_max != 4294967295):
									print "Client requested new range. Prev_max was", prev_max, "Client can compute", mods_per_sec, "mods per second"
									send_string = "<request type=\"new_range\" sender=\"manage\">"
									#Special case: Initial request
									if(prev_max == 0):
										send_string += "<min value=\"2\"/>"
									else: 
										send_string += "<min value=\""
										send_string += str(prev_max + 1)
										send_string += "\"/>"
									send_string += "<max value=\""
									new_max = next_max(prev_max, mods_per_sec)
									if(new_max > 4294967295): #bigger than client can handle
										new_max = 4294967295
									send_string += str(new_max)
									send_string += "\"/>"
									send_string += "</request>"
									sock.send(send_string)
									signal.alarm(45) #Set timeout for packet
								else: #If it has reached upper limits, terminate it
									terminate_computes()
							elif(request_type == "new_perfect"):
								x = xmldoc.getElementsByTagName('new_perfect')[0]
								new_perfect = x.attributes['value'].value
								#Append this number to the list if it doesn't already exist in it
								if new_perfect not in perfect_numbers:
									perfect_numbers.append(new_perfect)
								print "Found a new perfect number. Appended it to the list. Currently, perfect numbers are:", perfect_numbers
						elif(client == "report"):
							if(request_type == "query"):
								print "Sending performance characteristics of clients to report process."
								querystring = "<request type=\"report_data\" sender=\"manage\">"
								#Send current compute processes
								if compute_processes: 
									for hostname, mods_per_sec in compute_processes.iteritems():
										querystring += "<client addr=\""
										querystring += hostname
										querystring += "\" "
										querystring += "mods_per_sec=\""
										querystring += str(mods_per_sec)
										querystring += "\"/>"
								#Send found perfect numbers
								if perfect_numbers: 
									for perfect_number in perfect_numbers:
										querystring += "<perfect_number value=\""
										querystring += perfect_number
										querystring += "\"/>"
								querystring += "</request>"
								sock.send(querystring)
							elif(request_type == "terminate"):
								#To do: need to keep track of sockets that compute processes use and issue terminate signal over those sockets.
								print "Report sent terminate request. Terminate compute processes"
								terminate_computes()
