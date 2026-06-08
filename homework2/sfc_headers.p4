/* P4_16 SFC Header definitions boilerplate */

header ethernet_t {
    bit<48> dstAddr;
    bit<48> srcAddr;
    bit<16> etherType;
}

header mpls_t {
    bit<20> label;
    bit<3>  tc;
    bit<1>  bos;
    bit<8>  ttl;
}

/* Simplified 8-byte NSH Header as per project specs */
header nsh_t {
    bit<2>  ver;
    bit<1>  o;
    bit<1>  u;
    bit<6>  ttl;
    bit<6>  length;
    bit<8>  mdtype;
    bit<8>  next_proto;
    bit<24> spi; // Service Path Identifier
    bit<8>  si;  // Service Index
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLen;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> srcAddr;
    bit<32> dstAddr;
}
