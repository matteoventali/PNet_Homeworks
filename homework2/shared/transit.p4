/* =========================================================================
 * P4 Program: transit.p4
 * Description: Basic underlay/overlay transit logic for SFC architecture.
 * Nodes: B, C, F, G, H
 * Features: 
 * - IPv4 Longest Prefix Match (LPM) routing for return traffic.
 * - MPLS Exact Match forwarding/swapping for SFC tunnel traffic.
 * ========================================================================= */

#include <core.p4>
#include <v1model.p4>

/* =========================================================================
 * HEADERS & TYPES DEFINITIONS
 * ========================================================================= */

typedef bit<9>  egressSpec_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

const bit<16> TYPE_IPV4 = 0x800;

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

header ipv4_t {
    bit<4>    version;
    bit<4>    ihl;
    bit<8>    diffserv;
    bit<16>   totalLen;
    bit<16>   identification;
    bit<3>    flags;
    bit<13>   fragOffset;
    bit<8>    ttl;
    bit<8>    protocol;
    bit<16>   hdrChecksum;
    ip4Addr_t srcAddr;
    ip4Addr_t dstAddr;
}

struct metadata {
    // Empty metadata struct for this basic transit node
}

struct headers {
    ethernet_t ethernet;
    ipv4_t     ipv4;
}

/* =========================================================================
 * PARSER
 * ========================================================================= */

parser MyParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition accept;
    }
}

/* =========================================================================
 * CHECKSUM VERIFICATION
 * ========================================================================= */

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/* =========================================================================
 * INGRESS PROCESSING
 * ========================================================================= */

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    action drop() {
        mark_to_drop(standard_metadata);
    }

    /* --- IPv4 ACTIONS & TABLES (For Return Traffic) --- */
    
    action ipv4_forward(macAddr_t dstAddr, egressSpec_t port) {
        // Update MAC addresses for the next hop
        hdr.ethernet.srcAddr = hdr.ethernet.srcAddr;
        hdr.ethernet.dstAddr = dstAddr;

        // Decrement TTL
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
        
        // Set the output port
        standard_metadata.egress_spec = port;
    }

    table ipv4_lpm {
        key = {
            hdr.ipv4.dstAddr: lpm;
        }
        actions = {
            ipv4_forward;
            drop;
            NoAction;
        }
        size = 1024;
        default_action = drop();
    }

    /* --- APPLY LOGIC --- */
    apply {
        // If the packet has an MPLS header, process it via the MPLS table
        if (hdr.ipv4.isValid()) {
            ipv4_lpm.apply();
        }
    }
}

/* =========================================================================
 * EGRESS PROCESSING
 * ========================================================================= */

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {
    apply {
        // No specific egress logic required for standard transit nodes
    }
}

/* =========================================================================
 * CHECKSUM COMPUTATION
 * ========================================================================= */

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version,
              hdr.ipv4.ihl,
              hdr.ipv4.diffserv,
              hdr.ipv4.totalLen,
              hdr.ipv4.identification,
              hdr.ipv4.flags,
              hdr.ipv4.fragOffset,
              hdr.ipv4.ttl,
              hdr.ipv4.protocol,
              hdr.ipv4.srcAddr,
              hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16
        );
    }
}

/* =========================================================================
 * DEPARSER
 * ========================================================================= */

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        // Emit headers in the exact order they should appear on the wire
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
    }
}

/* =========================================================================
 * SWITCH INSTANTIATION
 * ========================================================================= */

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;
