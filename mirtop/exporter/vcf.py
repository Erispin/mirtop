from __future__ import print_function

import datetime
import sys
import os.path as op

import six

from mirtop.mirna.fasta import read_precursor
from mirtop.mirna.mapper import read_gtf_to_precursor, read_gtf_to_mirna
from mirtop.gff.body import read_gff_line
import mirtop.libs.logger as mylog

logger = mylog.getLogger(__name__)


def convert(args):
    for fn in args.files:
        out_file = op.join(args.out, "%s.vcf" % op.splitext(op.basename(fn))[0])
        logger.info("Reading %s" % fn)
        create_vcf(fn, args.hairpin, args.gtf, out_file)
        logger.info("VCF generated %s" % out_file)


def cigar_length(cigar):
    """
    Args:
        'cigar(str)': CIGAR standard of a compressed alignment representation, this CIGAR omits the '1' integer.
    Returns:
        'total_n(int)': CIGAR length in nucleotides.
    """
    total_n = 0
    match_n = "0"
    for i in cigar:
        if i.isdigit():
            match_n = match_n + str(i)
        else:
            total_n = total_n + int(match_n) + 1
            if i == "D" or (i == "M" and match_n != "0"):
                total_n = total_n - 1
            match_n = "0"
    return(total_n)
def cigar_2_key(cigar_read, cigar_ref, readseq, refseq, pos):
    """
    Args:
        'cigar_read(str)': CIGAR extended string of the read sequence, output of the expand_seqs.
        'cigar_ref(str)': CIGAR extended string of the reference sequence, output of the expand_seqs.
        'readseq(str)': the read sequence
        'refseq(str)': the reference sequence
        'pos(str)': the initial current position of the chromosome.
    Returns:
        'key_pos(str list)': a list with the positions of the variances.
        'key_var(str list)': a list with the variant keys found.
        'ref(str list)': reference base(s).
        'alt(str list)': altered base(s).
    """
    key_pos = []
    key_var = []
    ref = []
    alt = []
    n_I = 0  # To balance the position between read and ref sequences
    for i in range(len(cigar_ref)):  # Parsing for SNPs and Dels
        if cigar_ref[i] == "M":
            continue
        elif cigar_ref[i] in ["A", "T", "C", "G"]:
            key_pos.append(pos + i+1 + n_I)
            key_var.append(cigar_ref[i])
            ref.append(refseq[i])
            alt.append(cigar_ref[i])
        elif cigar_ref[i] == "D":
            if i == 0:
                print("Unexpected 'D' in the first position of CIGAR")
            elif i > 0:
                if cigar_ref[i-1] == "D":
                    ref[-1] = ref[-1] + refseq[i]  # Adds new Del in the REF column
                    key_var[-1] = "D" + str(int(key_var[-1][1:]) + 1)  # Adds 1 to the number of Dels in succession
                else:
                    key_pos.append(pos + i + n_I)
                    key_var.append("D1")
                    ref.append(refseq[i-1:i+1])
                    alt.append(refseq[i-1])
    for i in range(len(cigar_read)):  # Parsing for Insertions
        if cigar_read[i] == "I":
            if i == 0:
                print("Unexpected 'I' in the first position of CIGAR")
            elif cigar_read[i-1] == "I":
                alt[-1] = alt[-1] + readseq[i]  # Adds the new Insert in the ALT column
                key_var[-1] = key_var[-1] + 'I' + readseq[i]  # Adds new Ins in the Key
                n_I = n_I - 1
            else:
                key_pos.append(pos + i + n_I)
                alt.append(readseq[i-1:i+1])
                ref.append(readseq[i-1])
                key_var.append("I" + alt[-1][-1])
                n_I = n_I - 1
        else:
            continue
    return (key_pos, key_var, ref, alt)
def expand_seqs(cigar):
    n_Mpar = "0"
    cigar_exp = ""
    for i in cigar:
        if i.isdigit():
            n_Mpar = n_Mpar + i  # Gets the number of matched nucleotides
        elif i == "M":
            if n_Mpar == "0":
                cigar_exp = cigar_exp + "M"
            else:
                cigar_exp = cigar_exp + str(''.join("M"*int(n_Mpar)))  # Adds all the "M"s
                n_Mpar = "0"
        elif i in ["A", "T", "C", "G", "I", "D"]:
            cigar_exp = cigar_exp + i
        else:
            print("Unexpected value")
    cigar_exp_read = cigar_exp.replace("D", "")
    cigar_exp_ref = cigar_exp.replace("I", "")
    return(cigar_exp_read, cigar_exp_ref)

def adapt_refseq(cigar_ref, hairpin, parent_ini_pos, var5p):
    index = parent_ini_pos + var5p
    max_index = index + len(cigar_ref)
    refseq = hairpin[index:max_index]
    return(refseq)

def create_vcf(mirgff3, precursor, gtf, vcffile):
    """
    Args:
        'mirgff3(str)': File with mirGFF3 format that will be converted
        'precursor(str)': FASTA format sequences of all miRNA hairpins
        'gtf(str)': Genome coordinates
        'vcffile': name of the file to be saved
    Returns:
        Nothing is returned, instead, a VCF file is generated
    """
    #Check if the input files exist:
    try:
        gff3_file = open(mirgff3, "r")
    except IOError:
        print ("Can't read the file", end=mirgff3)
        sys.exit()
    with gff3_file:
        data = gff3_file.read().decode("utf-8-sig").encode("utf-8")
    gff3_data = data.split("\n")
    vcf_file = open(vcffile, "w")
    ver = "v4.3"  # Current VCF version formatting
    vcf_file.write("##fileformat=VCF%s\n" % ver)
    date = datetime.datetime.now().strftime("%Y%m%d")
    vcf_file.write("##fileDate=%s\n" % date)
    source = "\n".join(s for s in gff3_data if "## source-ontology: " in s)[20:]
    line = 0
    sample_names = []
    while gff3_data[line][:2] == "##":
        if gff3_data[line][:19] == "## source-ontology:":
            source = gff3_data[line][20:]
        elif gff3_data[line][:11] == "## COLDATA:":
            sample_names = gff3_data[line][12:].split(",")
        line += 1
    vcf_file.write("##source=%s\n" % source)
    # ref_file = "N/A"  # Temporary
    # vcf_file.write("##reference=%s\n" % ref_file)
    vcf_file.write('##INFO=<ID=NS,Type=Integer,Description="Number of samples"\n')
    vcf_file.write("##FILTER=<ID=REJECT,Description='"'Filter not passed'"'>\n")
    vcf_file.write('##FORMAT=<ID=TRC,Number=1,Type=Integer,Description="Total read count">\n')
    vcf_file.write('##FORMAT=<ID=TSC,Number=1,Type=Integer,Description="Total SNP count">\n')
    vcf_file.write('##FORMAT=<ID=TMC,Number=1,Type=Integer,Description="Total miRNA count">\n')
    vcf_file.write('##FORMAT=<ID=GT,Number=1,Type=Integer,Description="Genotype">\n')
    header = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
    for s in range(len(sample_names)):
        header = header + "\t" + sample_names[s]
    vcf_file.write(header)
    hairpins = read_precursor(precursor)
    gff3 = read_gtf_to_precursor(gtf)
    gtf_dic = read_gtf_to_mirna(gtf)
    all_dict = dict()  # initializing an empty dictionary where all info will be added
    key_list = []  # Initializing a list which will contain all the keys of the dictionary
    mirna_dict = dict()  # initializing an empty dictionary where mirna info will be put
    n_SNP = 0
    n_noSNP = 0
    no_var = 0
    for line in range(0, len(gff3_data)):
        if len(gff3_data[line]) == 0:
            continue
        elif gff3_data[line][1] == "#":
            continue
        else:
            gff_fields = read_gff_line(gff3_data[line])
            gtf_name = gff_fields['attrb']['Name']
            gtf_parent = gff_fields['attrb']['Parent']
            if gtf_parent not in gff3:
                continue
            if gtf_name not in gff3[gtf_parent]:
                continue
            parent_ini_pos = gff3[gtf_parent][gtf_name][0]
            # parent_end_pos = gff3[gtf_parent][gtf_name][1] not used
            vcf_chrom = gtf_dic[gtf_name][gtf_parent][0]
            vcf_pos = int(gff_fields['start']) + int(gtf_dic[gtf_name][gtf_parent][1])
            hairpin = hairpins[gtf_parent]
            variants = gff_fields['attrb']['Variant'].split(",")
            cigar = gff_fields['attrb']["Cigar"]
            readseq = gff_fields['attrb']['Read']
            var5p = [s for s in variants if 'iso_5p' in s]  # Obtaining iso_5p value:
            if len(var5p):
                var5p = int(var5p[0][7:])  # Position of iso_5p value
            else:
                var5p = 0  # 0 if iso_5p is not found
            (cigar_exp_read, cigar_exp_ref) = expand_seqs(cigar)
            refseq = adapt_refseq(cigar_exp_ref, hairpin, parent_ini_pos, var5p)
            (key_pos, key_var, vcf_ref, vcf_alt) = cigar_2_key(cigar_exp_read, cigar_exp_ref, readseq, refseq,
                                                               (vcf_pos + var5p))
            if len(key_var) > 0:
                for s in range(len(key_var)):
                    key_dict = vcf_chrom + '-' + str(key_pos[s]) + '-' + str(key_var[s])
                    raw_counts = gff_fields['attrb']['Expression']
                    raw_counts = [int(i) for i in raw_counts.split(',')]
                    nozero_counts = [int(i > 0) for i in raw_counts]  # counts for every sample if expr != 0.
                    if str(key_var[s]) in ["A", "C", "T", "G"]:
                        if gtf_name in mirna_dict:  # Adding expression values to same mirnas
                            mirna_dict[gtf_name]['Z'] = [sum(x) for x in zip(mirna_dict[gtf_name]['Z'], raw_counts)]
                        else:
                            mirna_dict[gtf_name] = {}
                            mirna_dict[gtf_name]["Z"] = raw_counts
                    if key_dict in all_dict:
                        if all_dict[key_dict]["Type"] in ["A", "C", "T", "G"]:
                            all_dict[key_dict]['X'] = [sum(x) for x in zip(all_dict[key_dict]['X'], nozero_counts)]
                            all_dict[key_dict]['Y'] = [sum(x) for x in zip(all_dict[key_dict]['Y'], raw_counts)]
                    else:
                        key_list.append(key_dict)
                        all_dict[key_dict] = {}
                        all_dict[key_dict]["Chrom"] = vcf_chrom
                        all_dict[key_dict]["Position"] = key_pos[s]
                        all_dict[key_dict]["mirna"] = gtf_name
                        all_dict[key_dict]["Type"] = key_var[s]
                        if key_var[s][0] in ["A", "C", "T", "G"]:
                            n_SNP += 1
                            all_dict[key_dict]["SNP"] = True
                            all_dict[key_dict]["ID"] = gff_fields['attrb']['Name'] + '-SNP' + str(n_SNP)
                            all_dict[key_dict]['X'] = nozero_counts
                            all_dict[key_dict]['Y'] = raw_counts
                        else:
                            n_noSNP += 1
                            all_dict[key_dict]["SNP"] = False
                            all_dict[key_dict]["ID"] = gff_fields['attrb']['Name'] + '-nonSNP' + str(n_noSNP)
                        all_dict[key_dict]["Ref"] = vcf_ref[s]
                        all_dict[key_dict]["Alt"] = vcf_alt[s]
                        all_dict[key_dict]["Qual"] = "."
                        all_dict[key_dict]["Filter"] = gff_fields['attrb']['Filter']
                        all_dict[key_dict]["Info"] = "NS=" + str(len(sample_names))
            else:
                no_var += 1
    #  Writing the VCF file:
    for s in key_list:
        variant_line = ("\n%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" %
                       (all_dict[s]["Chrom"], all_dict[s]["Position"], all_dict[s]["ID"],
                        all_dict[s]["Ref"], all_dict[s]["Alt"], all_dict[s]["Qual"],
                        all_dict[s]["Filter"], all_dict[s]["Info"]))
        if all_dict[s]["Type"] in ["A", "T", "C", "G"]:
            format_col = "TRC:TSC:TMC:GT"
            variant_line = variant_line + "\t" + format_col
            samples = ""
            for n in range(len(sample_names)):
                X = all_dict[s]["X"][n]
                Y = all_dict[s]["Y"][n]
                Z = mirna_dict[all_dict[s]["mirna"]]["Z"][n]
                if Y == 0:
                    GT = "0|0"
                elif Z == Y:
                    GT = "1|1"
                else:
                    GT = "1|0"
                samples = samples + "\t" + str(X) + ":" + str(Y) + ":" + str(Z) + ":" + GT
            variant_line = variant_line + samples
        else:
            format_col = ""
            variant_line = variant_line + format_col
        vcf_file.write(variant_line)
    vcf_file.close()