# -*- coding: utf-8 -*-
##
## This file is part of Invenio.
## Copyright (C) 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012 CERN.
##
## Invenio is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## Invenio is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Invenio; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

from invenio.config import CFG_BIBAUTHORID_SEARCH_ENGINE_MAX_DATACHUNK_PER_INSERT_DB_QUERY
from invenio.bibauthorid_config import QGRAM_LEN, MATCHING_QGRAMS_PERCENTAGE, \
        MAX_T_OCCURANCE_RESULT_LIST_CARDINALITY, MIN_T_OCCURANCE_RESULT_LIST_CARDINALITY, \
        MAX_NOT_MATCHING_NAME_CHARS, PREFIX_SCORE_COEFFICIENT, NAME_SCORE_COEFFICIENT

""" Author search engine. """

from threading import Thread
from operator import itemgetter
from math import ceil
from msgpack import packb as serialize
from msgpack import unpackb as deserialize

from invenio.textutils import translate_to_ascii
from invenio.intbitset import intbitset
from invenio.bibauthorid_name_utils import create_indexable_name, distance
from bibauthorid_dbinterface import get_name_string_to_pid_dictionary, get_indexable_name_personids, get_inverted_lists, \
                                    set_inverted_lists_ready, set_dense_index_ready, trancate_table, \
                                    insert_multiple_values



def get_qgrams_from_string(string, q):
    '''
    docstring

    @param string:
    @type string:
    @param q:
    @type q:

    @return:
    @rtype:
    '''
    qgrams = list()

    for i in range(len(string)+1-q):
        qgrams.append(string[i:i+q])

    return qgrams

def populate_table_with_limit(table_name, column_names, args, args_tuple_size, \
                              max_insert_size=CFG_BIBAUTHORID_SEARCH_ENGINE_MAX_DATACHUNK_PER_INSERT_DB_QUERY):
    '''
    docstring

    @param table_name:
    @type table_name:
    @param column_names:
    @type column_names:
    @param args:
    @type args:
    @param args_tuple_size:
    @type args_tuple_size:
    @param max_insert_size:
    @type max_insert_size:
    '''
    column_num = len(column_names)
    summ = 0
    start = 0

    for i in range(len(args_tuple_size)):
        if summ+args_tuple_size[i] <= max_insert_size:
            summ += args_tuple_size[i]
            continue
        summ = args_tuple_size[i]
        insert_multiple_values(table_name, column_names, args[start:(i-1)*column_num])
        start = (i-1)*column_num

    insert_multiple_values(table_name, column_names, args[start:])


def populate_table(table_name, column_names, args, empty_table_first=True):
    '''
    docstring

    @param table_name:
    @type table_name:
    @param column_names:
    @type column_names:
    @param args:
    @type args:
    @param empty_table_first:
    @type empty_table_first:
    '''
    args_len = len(args)
    column_num = len(column_names)
    args_tuple_size = list()

    assert args_len % column_num == 0, 'Trying to populate table %s. Wrong number of arguments passed.' % table_name

    for i in range(args_len/column_num):
        args_tuple_size.append(sum([len(str(i)) for i in args[i*column_num:i*column_num+column_num]]))

    if empty_table_first:
        trancate_table(table_name)

    populate_table_with_limit(table_name, column_names, args, args_tuple_size)

def create_dense_index(name_pids_dict, names_list):
    '''
    docstring

    @param name_pids_dict:
    @type name_pids_dict:
    @param names_list:
    @type names_list:
    '''
    name_id = 0
    args = list()

    for name in names_list:
        personids = name_pids_dict[name]
        args += [name_id, name, serialize(list(personids))]
        name_id += 1

    populate_table('denseINDEX', ['name_id','person_name','personids'], args)
    set_dense_index_ready()

def create_inverted_lists(name_pids_dict, names_list):
    '''
    docstring

    @param name_pids_dict:
    @type name_pids_dict:
    @param names_list:
    @type names_list:
    '''
    name_id = 0
    inverted_lists = dict()

    for name in names_list:
        qgrams = set(get_qgrams_from_string(name, QGRAM_LEN))
        for qgram in qgrams:
            try:
                inverted_list, cardinality = inverted_lists[qgram]
                inverted_list.add(name_id)
                inverted_lists[qgram][1] = cardinality + 1
            except KeyError:
                inverted_lists[qgram] = [set([name_id]), 1]
        name_id += 1

    args = list()

    for qgram in inverted_lists.keys():
        inverted_list, cardinality = inverted_lists[qgram]
        args += [qgram, serialize(list(inverted_list)), cardinality]

    populate_table('invertedLISTS', ['qgram','inverted_list','list_cardinality'], args)
    set_inverted_lists_ready()

def create_bibauthorid_indexer():
    '''
    docstring
    '''
    name_pids_dict = get_name_string_to_pid_dictionary()
    indexable_name_pids_dict = dict()

    for name in name_pids_dict.keys():
        indexable_name = create_indexable_name(translate_to_ascii(name)[0])
        if indexable_name:
            indexable_name_pids_dict[indexable_name] = name_pids_dict[name]

    indexable_names_list = indexable_name_pids_dict.keys()

    threads = list()
    threads.append(Thread(target=create_dense_index, args=(indexable_name_pids_dict, indexable_names_list)))
    threads.append(Thread(target=create_inverted_lists, args=(indexable_name_pids_dict, indexable_names_list)))

    for t in threads:
        t.start()

    for t in threads:
        t.join()

def solve_T_occurence_problem(query_string):
    '''
    docstring

    @param query_string:
    @type query_string:

    @return:
    @rtype:
    '''
    query_string_qgrams = get_qgrams_from_string(query_string, QGRAM_LEN)
    query_string_qgrams_set = set(query_string_qgrams)
    if not query_string_qgrams_set:
        return None

    inverted_lists = get_inverted_lists(query_string_qgrams_set)
    if not inverted_lists:
        return None

    inverted_lists = sorted(inverted_lists, key=itemgetter(1), reverse=True)
    T = int(MATCHING_QGRAMS_PERCENTAGE * len(inverted_lists))
    nameids = intbitset(deserialize(inverted_lists[0][0]))

    for i in range(1, T):
        inverted_list = intbitset(deserialize(inverted_lists[i][0]))
        nameids &= inverted_list

    for i in range(T, len(inverted_lists)):
        if len(nameids) < MAX_T_OCCURANCE_RESULT_LIST_CARDINALITY:
            break
        inverted_list = intbitset(deserialize(inverted_lists[i][0]))
        nameids_temp = inverted_list & nameids
        if len(nameids_temp) > MIN_T_OCCURANCE_RESULT_LIST_CARDINALITY:
            nameids = nameids_temp
        else:
            break

    return nameids

def calculate_name_score(query_string, nameids):
    '''
    docstring

    @param query_string:
    @type query_string:
    @param nameids:
    @type nameids:

    @return:
    @rtype:
    '''
    name_personids_list = get_indexable_name_personids(nameids)
    query_string_qgrams = get_qgrams_from_string(query_string, QGRAM_LEN)
    query_string_qgrams_len = len(query_string_qgrams)
    distance_threshold = ceil(len(query_string)*MAX_NOT_MATCHING_NAME_CHARS) + 2
    name_score_list = list()

    for name, personids in name_personids_list:

        dist = distance(name, query_string)

        if dist < distance_threshold:
            current_string_qgrams = get_qgrams_from_string(name, QGRAM_LEN)
            limit = min([query_string_qgrams_len, len(current_string_qgrams)])
            prefix_score = sum([1/float(2**(i+1)) for i in range(limit) if query_string_qgrams[i] == current_string_qgrams[i]])
            name_score = (1-PREFIX_SCORE_COEFFICIENT)*(1-dist/float(distance_threshold)) + PREFIX_SCORE_COEFFICIENT*(prefix_score/(1-1/float(2**query_string_qgrams_len)))
            name_score_list.append((name, name_score, deserialize(personids)))

    return name_score_list

def calculate_pid_score(names_score_list):
    '''
    docstring

    @param names_score_list:
    @type names_score_list:

    @return:
    @rtype:
    '''
    max_appearances = 1
    pid_metrics_dict = dict()

    for name, name_score, personids in names_score_list:
        for pid in personids:
            try:
                appearances = pid_metrics_dict[pid][2]+1
                pid_metrics_dict[pid][2] = appearances
                if appearances > max_appearances:
                    max_appearances = appearances
            except KeyError:
                pid_metrics_dict[pid] = [name, name_score, 1]

    pids_score_list = list()

    for pid in pid_metrics_dict.keys():
        name, name_score, appearances = pid_metrics_dict[pid]
        final_score = NAME_SCORE_COEFFICIENT*name_score + (1-NAME_SCORE_COEFFICIENT)*(appearances/float(max_appearances))
        pids_score_list.append((pid, name, final_score))

    return pids_score_list

def find_personids_by_name(query_string):
    '''
    docstring

    @param query_string:
    @type query_string:

    @return:
    @rtype:
    '''
    query_string = create_indexable_name(translate_to_ascii(query_string)[0])
    if not query_string:
        return None

    nameids = solve_T_occurence_problem(query_string)
    if not nameids:
        return None

    name_score_list = calculate_name_score(query_string, nameids)
    name_ranking_list = sorted(name_score_list, key=itemgetter(1), reverse=True)

    pid_score_list = calculate_pid_score(name_ranking_list)
    pids_ranking_list = sorted(pid_score_list, key=itemgetter(2), reverse=True)

    ranked_pid_name_list = [(pid, []) for pid, name, final_score in pids_ranking_list]

    return ranked_pid_name_list