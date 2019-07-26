# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ryu.base import app_manager
from ryu.controller import ofp_event, dpset
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER,set_ev_cls
from ryu.ofproto import ofproto_v1_3, ofproto_v1_3_parser
from ryu.lib.packet import packet,ethernet,ipv4,udp,tcp,icmp
from ryu.ofproto.ether import ETH_TYPE_IP, ETH_TYPE_ARP,ETH_TYPE_LLDP,ETH_TYPE_MPLS,ETH_TYPE_IPV6
from ryu.ofproto.inet import IPPROTO_ICMP, IPPROTO_TCP, IPPROTO_UDP,IPPROTO_SCTP
from parse_firewall_rules import parse_firewall
from switch_information import SwitchInfo
from packet_out import SendPacket
from construct_flow import Construct
from connection_tracking import TrackConnection
from packet_time import Incoming_packet_time
#from con_trck import parse_fw  ### delete  this after usage
ICMP_PING = 8
ICMP_PONG = 0
TCP_SYN = 0x02
TCP_SYN_ACK = 0x12
TCP_BOGUS_FLAGS = 0x15

class InefficientFirewall(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    inner_policy = {}
    icmp_conn_track = {}
    tcp_conn_track = {}
    udp_conn_track = {}
    sendpkt = SendPacket()
    flow = Construct()
    track = TrackConnection()
    current_time = Incoming_packet_time()

    def __init__(self, *args, **kwargs):
        super(InefficientFirewall, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        parser = parse_firewall()
        self.inner_policy = parser.parse()

 
    @set_ev_cls(dpset.EventDP, dpset.DPSET_EV_DISPATCHER)
    def handler_datapath(self, ev):
        SwitchInfo(ev)
    
    
    """
        Handles incoming packets. Decodes them
        and checks for suitable Firewall rules.
    """
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        try:
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
            ethtype = eth.ethertype
        
            out_port = self.port_learn(datapath, eth, in_port)
            action_fwd_to_out_port = [parser.OFPActionOutput(out_port)]
            action_drop = [parser.OFPActionOutput(ofproto.OFPPC_NO_FWD)]
            actions_default =  action_fwd_to_out_port
    
            if(out_port != ofproto.OFPP_FLOOD) and (ethtype == ETH_TYPE_IP):
                ipo = pkt.get_protocols(ipv4.ipv4)[0]
                
                
                # Check for ICMP
                if(ipo.proto == IPPROTO_ICMP):
                    flag1 = 0
                    icmpob = pkt.get_protocol(icmp.icmp)
                    
                    # Check if this is PING
                    if ((icmpob.type==ICMP_PING) and self.inner_policy.has_key(ipo.src)):
                        temp = self.inner_policy.get(ipo.src)
                        for i in range(0,len(temp)):
                            if temp[i][0] == ipo.dst:
                                xyz = temp[i]
                                if((xyz[1]=='ICMP') and (xyz[5] == 'ALLOW')):
                                    flag1 = 1
                                    actions_default = action_fwd_to_out_port
                                    self.icmp_conn_track = self.track.conn_track_dict(self.icmp_conn_track,ipo.src, ipo.dst, "PING", "PONG", xyz[5],2)
                                    self.logger.info("%s  ->  %s : ECHO REQUEST ALLOWED" % (ipo.src,ipo.dst))
                                    break
                                
                    # Otherwise, PONG...!            
                    elif((icmpob.type == ICMP_PONG) and (self.icmp_conn_track.has_key(ipo.src))):
                        temp2 = self.icmp_conn_track.get(ipo.src)
                        for i in range(0,len(temp2)): 
                            if temp2[i][0] == ipo.dst:
                                xyz = temp2[i]
                                if ((xyz[1]=='PONG') and (xyz[3] == 'ALLOW')):
                                    flag1 = 1
                                    actions_default = action_fwd_to_out_port
                                    self.logger.info("%s  ->  %s : ECHO REPLY ALLOWED" % (ipo.src,ipo.dst))
                                    self.logger.info("\n%s  ->  %s  action= %s  state= ESTABLISHED \n" % (xyz[0],ipo.src,xyz[2]))
                                    self.current_time.time()
                                    self.logger.info("\n")  
                                    break
                    
                    # What about no match ??            
                    if (flag1 == 0):
                        actions_default = action_drop
                        self.logger.info("%s  ->  %s : BLOCKED" % (ipo.src,ipo.dst))
                    
                  
                # Check for TCP   
                elif (ipo.proto == IPPROTO_TCP):
                    tcpo = pkt.get_protocol(tcp.tcp)
                    flag2 = 0
                    
                    # TCP SYN packet
                    if (((tcpo.bits & TCP_SYN) == TCP_SYN) & ((tcpo.bits & TCP_BOGUS_FLAGS) == 0x00)):
                        if self.inner_policy.has_key(ipo.src):
                            temp = self.inner_policy.get(ipo.src)
                            for i in range(0,len(temp)):
                                if ((temp[i][0] == ipo.dst) and (temp[i][1]=='TCP') and (int(temp[i][2]) == tcpo.src_port) and (int(temp[i][3]) == tcpo.dst_port)  and  (temp[i][5] == 'ALLOW')):
                                    flag2 = 1
                                    actions_default = action_fwd_to_out_port
                                    self.tcp_conn_track = self.track.conn_track_dict(self.tcp_conn_track,ipo.src, ipo.dst, tcpo.src_port, tcpo.dst_port , tcpo.seq,1)
                                    self.logger.info("%s  ->  %s : SYN ALLOWED" % (ipo.src,ipo.dst))
                                    break
                    
                    # TCP SYN ACK packet                   
                    elif((tcpo.bits & TCP_SYN_ACK) == TCP_SYN_ACK):
                        if self.tcp_conn_track.has_key(ipo.dst):
                            temp2 = self.tcp_conn_track.get(ipo.dst)
                            for i in range(0,len(temp2)):
                                if ((temp2[i][0] == ipo.src) and (int(temp2[i][1]) == tcpo.dst_port) and (int(temp2[i][2]) == tcpo.src_port)):
                                    flag2 = 1 
                                    actions_default = action_fwd_to_out_port
                                    self.tcp_conn_track = self.track.conn_track_dict(self.tcp_conn_track,ipo.src, ipo.dst, tcpo.src_port, tcpo.dst_port, tcpo.seq,1)
                                    self.logger.info("%s  ->  %s : SYN ACK ALLOWED" % (ipo.src,ipo.dst))
                                    self.logger.info("\n%s  ->  %s  src_port= %s  dst_port= %s  state= ESTABLISHED \n" % (ipo.dst,ipo.src,temp2[i][1],temp2[i][2]))
                                    self.current_time.time()
                                    self.logger.info("\n")  
                                    break
                    
                    # All remaining TCP packets, like, ACK, PUSH, FIN etc.            
                    else:
                        if self.tcp_conn_track.has_key(ipo.src):
                            temp3 = self.tcp_conn_track.get(ipo.src)
                            for i in range(0,len(temp3)):
                                if ((temp3[i][0] == ipo.dst) and (int(temp3[i][1]) == tcpo.src_port) and (int(temp3[i][2]) == tcpo.dst_port)):
                                    flag2 = 1
                                    actions_default = action_fwd_to_out_port
                                    self.logger.info("%s  ->  %s : TRANSMISSION ALLOWED" % (ipo.src,ipo.dst))
                                    break
                      
                    # What if no match found ?                
                    if (flag2 == 0):
                        actions_default = action_drop
                        self.logger.info("%s  ->  %s : SYN BLOCKED" % (ipo.src,ipo.dst))
                           
                                
                # Check for UDP
                elif (ipo.proto == IPPROTO_UDP):
                    flag3 = 0 
                    udpo = pkt.get_protocol(udp.udp)
                    
                    # Check for tracked UDP
                    if self.udp_conn_track.has_key(ipo.dst):
                        tmp_tpl = self.udp_conn_track.get(ipo.dst)
                        tmp = list(tmp_tpl)
                        for i in range(0,len(tmp)):
                            if (tmp[i][0] == ipo.src):
                                xyz = tmp[i]
                                if ((int(xyz[1]) == udpo.dst_port) and (int(xyz[2]) == udpo.src_port) and (xyz[3] == 'UNREPLIED')):
                                    flag3 = 1
                                    self.logger.info("%s  ->  %s  : UDP PACKET ALLOWED" % (ipo.src,ipo.dst))
                                    actions_default = action_fwd_to_out_port  
                                    del tmp[i]        ###  We need to remove this state entry to keep this packet 'REPLIED'
                                    self.logger.info("\n%s  ->  %s  src_port= %s  dst_port= %s  state= ASSURED\n" % (ipo.src,ipo.dst, udpo.src_port, udpo.dst_port))
                                    break           
                        tmp_tpl = tuple(tmp)
                        if len(tmp_tpl) != 0:
                            self.udp_conn_track[ipo.dst] = tmp_tpl
                        else:
                            self.udp_conn_track.pop(ipo.dst,None)
                            
                    # Check for first UDP packet
                    elif self.inner_policy.has_key(ipo.src):
                        temp = self.inner_policy.get(ipo.src)
                        for i in range(0,len(temp)):
                            if temp[i][0] == ipo.dst:
                                xyz = temp[i]
                                if((xyz[1]=='UDP') and (int(xyz[2]) == udpo.src_port) and (int(xyz[3]) == udpo.dst_port) and (xyz[5] == 'ALLOW')):
                                    flag3 = 1
                                    actions_default = action_fwd_to_out_port  
                                    self.udp_conn_track = self.track.conn_track_dict(self.udp_conn_track,ipo.src, ipo.dst, udpo.src_port,udpo.dst_port , "UNREPLIED",1)
                                    self.logger.info("%s  ->  %s  : UDP PACKET ALLOWED" % (ipo.src,ipo.dst))
                                    self.logger.info("\n%s  ->  %s  src_port= %s  dst_port= %s  state= UNREPLIED\n" % (ipo.src,ipo.dst, udpo.src_port, udpo.dst_port))
                                    self.current_time.time()
                                    self.logger.info("\n")  
                                    break
                    
                    # what if no match found ?
                    if (flag3 == 0):
                        actions_default = action_drop
                        self.logger.info("%s  ->  %s : UDP BLOCKED" % (ipo.src,ipo.dst))
                
                
                # If not ICMP, TCP or UDP then drop..!           
                else:
                    self.logger.info("Wrong IP protocol found")
                    actions_default = action_drop
            
            
            # Handling ARP Rules.
            elif(ethtype == ETH_TYPE_ARP):
                self.arp_handling(datapath, out_port, eth, in_port)
                actions_default = action_fwd_to_out_port
                
                
            # If packet is not IP or ARP then drop..!    
            else: 
                actions_default = action_drop
                
        except Exception as err:
            self.logger.info("MYERROR: %s" , err.message)
            action_drop = [parser.OFPActionOutput(ofproto.OFPPC_NO_FWD)]
            actions_default = action_drop
            
        finally:    
            self.sendpkt.send(datapath, msg, in_port, actions_default)
    
    
    """
        Add ARP rules on Flow Tables
    """
    def arp_handling(self,datapath, out_port, eth_obj, in_port):
        if(out_port != datapath.ofproto.OFPP_FLOOD):
            actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]
            self.flow.add_flow(datapath=datapath, actions=actions, priority=1000, in_port=in_port, 
                               eth_type= ETH_TYPE_ARP, eth_src= eth_obj.src, eth_dst = eth_obj.dst)        
    
            
    """
        Learn Switch port associated with a MAC address here
    """        
    def port_learn(self, datapath, eth_obj, in_port):
        try:
            self.mac_to_port.setdefault(datapath.id, {})
            self.mac_to_port[datapath.id][eth_obj.src] = in_port
            
            if (eth_obj.ethertype == ETH_TYPE_IP)  or  (eth_obj.ethertype == ETH_TYPE_ARP):
                    if eth_obj.dst in self.mac_to_port[datapath.id]:
                        out_port = self.mac_to_port[datapath.id][eth_obj.dst]
                    else:
                        out_port = datapath.ofproto.OFPP_FLOOD
        except Exception as err:
            self.info(err.message)
            out_port = datapath.ofproto.OFPP_FLOOD
        finally:
            return out_port