import utils
import pymongo
from xbrowse.core import constants
from xbrowse.parsers import vcf_stuff
from xbrowse.utils import compressed_file


class VariantAnnotator():

    def __init__(self, settings_module, reference, population_frequency_store, vep_annotator, custom_annotator=None):
        self.settings_module = settings_module
        self._db = pymongo.Connection()[settings_module.db_name]
        self._reference = reference
        self._population_frequency_store = population_frequency_store
        self._vep_annotator = vep_annotator
        self._custom_annotator = custom_annotator

    def ensure_indices(self):
        self._db.variants.ensure_index([('xpos', 1), ('ref', 1), ('alt', 1)])

    def _clear(self):
        self._db.drop_collection('variants')
        self.ensure_indices()

    def get_annotation(self, xpos, ref, alt, populations=None):
        doc = self._db.variants.find_one({'xpos': xpos, 'ref': ref, 'alt': alt})
        annotation = doc['annotation']
        if populations is not None:
            freqs = {}
            for p in populations:
                freqs[p] = annotation['freqs'].get(p, 0.0)
            annotation['freqs'] = freqs
        return annotation

    def add_variants_to_annotator(self, variant_t_list, force_all=False):
        """
        Make sure that all the variants in variant_t_list are in annotator
        For the ones that are not, go through the whole load cycle
        """
        if force_all:
            variants_to_add = variant_t_list
        else:
            variants_to_add = self._get_missing_annotations(variant_t_list)
        custom_annotations = None
        if self._custom_annotator:
            print "Getting custom annotations..."
            custom_annotations = self._custom_annotator.get_annotations_for_variants(variant_t_list)
        for variant_t, vep_annotation in self._vep_annotator.get_vep_annotations_for_variants(variants_to_add):
            annotation = {
                'vep_annotation': vep_annotation,
                'freqs': self._population_frequency_store.get_frequencies(variant_t[0], variant_t[1], variant_t[2]),
            }
            add_convenience_annotations(annotation)
            if self._custom_annotator:
                annotation.update(custom_annotations[variant_t])
            self._db.variants.update({
                'xpos': variant_t[0],
                'ref': variant_t[1],
                'alt': variant_t[2]
            }, {'$set': {'annotation': annotation},
            }, upsert=True)

    def add_vcf_file_to_annotator(self, vcf_file_path, force_all=False):
        """
        Add the variants in vcf_file_path to annotator
        Convenience wrapper around add_variants_to_annotator
        """
        print "Scanning VCF file first..."
        variant_t_list = []
        for variant_t in vcf_stuff.iterate_tuples(compressed_file(vcf_file_path)):
            variant_t_list.append(variant_t)
            if len(variant_t_list) == 100000:
                print "Adding another 100000 variants, through {}".format(variant_t_list[-1][0])
                self.add_variants_to_annotator(variant_t_list, force_all)
                variant_t_list = []
        self.add_variants_to_annotator(variant_t_list, force_all)

    def _get_missing_annotations(self, variant_t_list):
        ret = []
        for variant_t in variant_t_list:
            if not self._db.variants.find_one(
                {'xpos': variant_t[0],
                 'ref': variant_t[1],
                 'alt': variant_t[2]}):
                ret.append(variant_t)
        return ret

    def annotate_variant(self, variant, populations):
        annotation = self.get_annotation(variant.xpos, variant.ref, variant.alt, populations)
        variant.annotation = annotation

        # todo: gotta remove one
        # ...or actually maybe both
        variant.gene_ids = [g for g in annotation['gene_ids'] if self._reference.is_valid_gene_id(g)]
        variant.coding_gene_ids = [g for g in annotation['coding_gene_ids'] if self._reference.is_valid_gene_id(g)]


def add_convenience_annotations(annotation):
    """
    Add a bunch of convenience lookups to an annotation.
    This is kind of a historical relic - should try to remove as many as we can
    """
    vep_annotation = annotation['vep_annotation']
    annotation['gene_ids'] = utils.get_gene_ids(vep_annotation)
    annotation["coding_gene_ids"] = utils.get_coding_gene_ids(vep_annotation)
    annotation['worst_vep_annotation_index'] = utils.get_worst_vep_annotation_index(vep_annotation)
    annotation['worst_vep_index_per_gene'] = {}
    for gene_id in annotation['gene_ids']:
        annotation['worst_vep_index_per_gene'][gene_id] = utils.get_worst_vep_annotation_index(
            vep_annotation,
            gene_id=gene_id
        )

    per_gene = {}
    for gene_id in annotation['coding_gene_ids']:
        per_gene[gene_id] = utils.get_worst_vep_annotation_index(vep_annotation, gene_id=gene_id)
    annotation['worst_vep_index_per_gene'] = per_gene

    worst_vep_annotation = vep_annotation[annotation['worst_vep_annotation_index']]

    annotation['vep_consequence'] = None
    if worst_vep_annotation:
        annotation['vep_consequence'] = worst_vep_annotation['consequence']

    annotation['vep_group'] = None
    if worst_vep_annotation:
        annotation['vep_group'] = constants.ANNOTATION_GROUP_REVERSE_MAP[annotation['vep_consequence']]