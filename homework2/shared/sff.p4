#include <core.p4>
#include <v1model.p4>

/* =========================================================================
 * HEADERS & TYPES DEFINITIONS
 * ========================================================================= */
#define MAX_HEADER 10

typedef bit<9>  egressSpec_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

const bit<16> TYPE_IPV4 = 0x800;
const bit<16> TYPE_MPLS = 0x8847;

header ethernet_t {
    macAddr_t   dstAddr;
    macAddr_t   srcAddr;
    bit<16>     etherType;
}

header ipv4_t {
    bit<4>      version;
    bit<4>      ihl;
    bit<8>      diffserv;
    bit<16>     totalLen;
    bit<16>     identification;
    bit<3>      flags;
    bit<13>     fragOffset;
    bit<8>      ttl;
    bit<8>      protocol;
    bit<16>     hdrChecksum;
    ip4Addr_t   srcAddr;
    ip4Addr_t   dstAddr;
}

header mpls_t {
    bit<20>     label;
    bit<3>      exp;
    bit<1>      bos;
    bit<8>      ttl;
}

header nsh_t {
    bit<24>     spi;
    bit<8>      si;
}


struct metadata {
    // Empty metadata struct for this basic transit node
}

struct headers {
    ethernet_t              ethernet;
    mpls_t[MAX_HEADER]      mpls;
    nsh_t                   nsh;
    ipv4_t                  ipv4;
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
            TYPE_MPLS: parse_mpls;
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_mpls {
        packet.extract(hdr.mpls.next);
        transition select(hdr.mpls.last.bos, hdr.mpls.last.exp) {
            (0, _): parse_stack_mpls;
            (1, 0): parse_ipv4;
            (1, 1): parse_nsh;
            default: accept;
        }
    }

    state parse_stack_mpls {
        packet.extract(hdr.mpls.next);
        transition select(hdr.mpls.last.bos, hdr.mpls.last.exp) {
            (0, _): parse_stack_mpls;
            (1, 0): parse_ipv4;
            default: accept;
        }
    }

    state parse_nsh {
        packet.extract(hdr.nsh);
        transition accept;
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

    /* IPV4 FORWARDING */

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

    /* ----------------------------------------------------- */

    /* MPLS PROCESSING */
    action push(bit<20> label, egressSpec_t port) {
        // Creating the mpls header
        hdr.mpls.push_front(1);
        hdr.mpls[0].setValid();
        hdr.mpls[0].label = (bit<20>)label;
        hdr.mpls[0].exp = (bit<3>)0;
        hdr.mpls[0].bos = (bit<1>)0; // This header is not the last MPLS header
        hdr.mpls[0].ttl = (bit<8>)100;
        
        // Updating the ethertype
        hdr.ethernet.etherType = TYPE_MPLS;
        
        // Set the output port
        standard_metadata.egress_spec = port;
    }

    action pop(egressSpec_t port) {
        // Removing the mpls header
        if ( hdr.mpls[0].bos == 1 )
            hdr.ethernet.etherType = TYPE_IPV4;
        
        hdr.mpls[0].setInvalid();
        hdr.mpls.pop_front(1);

        // Set the output port
        standard_metadata.egress_spec = port;
    }

    action swap(bit<20> label, egressSpec_t port) {
        // Swapping the mpls label
        hdr.mpls[0].label = label;

        // Updating the ttl value
        hdr.mpls[0].ttl = hdr.mpls[0].ttl - 1;

        // Dropping if the ttl value is 0
        if (hdr.mpls[0].ttl == 0)
        {
            drop();
        }
        else
        {
            // Set the output port
            standard_metadata.egress_spec = port;
        }
    }

    action forward(egressSpec_t port) {
        // Updating the ttl value
        hdr.mpls[0].ttl = hdr.mpls[0].ttl - 1;

        // Dropping if the ttl value is 0
        if (hdr.mpls[0].ttl == 0)
        {
            drop();
        }
        else
        {
            // Set the output port
            standard_metadata.egress_spec = port;
        }
    }

    table mpls_exact {
        key = {
            hdr.mpls[0].label: exact;
        }

        actions = {
            push;
            pop;
            swap;
            drop;
            forward;
            NoAction;
        }
        default_action = drop();

        size = 1024;
    }

    /* ----------------------------------------------------- */

    /* NSH */
    action forward_to_sf(egressSpec_t port) {
        // Set the output port
        standard_metadata.egress_spec = port;

        // Embedding the SPI and SI inside the destination MAC address
        bit<48> spi_extended = (bit<48>)hdr.nsh.spi;
        bit<48> si_extended  = (bit<48>)hdr.nsh.si;
        hdr.ethernet.dstAddr = (spi_extended << 8) | si_extended;
        
        // Removing the NSH header
        hdr.nsh.setInvalid();

        // Removing the MPLS header
        // We're sure that there is only one MPLS header to be removed
        hdr.mpls[0].setInvalid();
        hdr.mpls.pop_front(1);

        // Updating the ethertype
        hdr.ethernet.etherType = TYPE_IPV4;
    }


    table nsh_exact {
        key = {
            hdr.nsh.spi: exact;
            hdr.nsh.si:  exact;
        }

        actions = {
            forward_to_sf;
            NoAction;
        }
        default_action = NoAction();

        size = 1024;
    }

    action nsh_proxy_reinject(bit<20>mpls_label, egressSpec_t out_port) 
    {
        // Extracting SPI and SI from ethernet SRC address
        bit<8>  current_si    = (bit<8>)(hdr.ethernet.srcAddr & 0xFF);
        bit<24> extracted_spi = (bit<24>)((hdr.ethernet.srcAddr >> 8) & 0xFFFFFF);
        
        // Computing the new service index
        bit<8>  next_si       = current_si - 1;

        // Incapsulating the packet in an NSH header and MPLS header
        // if the chain must continue
        if ( next_si > 0 )
        {
            hdr.nsh.setValid();
            hdr.nsh.spi = extracted_spi;
            hdr.nsh.si = next_si;
            
            hdr.ethernet.etherType = TYPE_MPLS;
            hdr.mpls.push_front(1);
            hdr.mpls[0].setValid();
            hdr.mpls[0].label = mpls_label;
            hdr.mpls[0].exp = 1;
            hdr.mpls[0].bos = 1;
            hdr.mpls[0].ttl = 100;
        }
        
        // Output port
        standard_metadata.egress_spec = out_port;
    }

    table sf_return_table {
        key = {
            hdr.ethernet.srcAddr: exact;
        }

        actions = {
            nsh_proxy_reinject;
            NoAction;
        }
        default_action = NoAction();

        size = 1024;
    }

    /* ----------------------------------------------------- */

    apply {
        // If NSH has been found
        if (hdr.nsh.isValid())
        {
            if (!nsh_exact.apply().hit)
            {
                mpls_exact.apply();
            }
        }
        else if (hdr.mpls[0].isValid()) // If mpls header is valid
        {
            mpls_exact.apply();
        }
        else if (hdr.ipv4.isValid()) // If ipv4 header is found and is valid
        {
            // Check if it a return packet from an SF
            if (!sf_return_table.apply().hit) 
            {
                // Try to forward with ipv4
                ipv4_lpm.apply();
            }
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
        packet.emit(hdr.mpls);
        packet.emit(hdr.nsh);
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
