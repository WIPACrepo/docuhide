from __future__ import print_function, unicode_literals, division

import argparse
from datetime import datetime
from itertools import islice
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


escape_illegal_xml_characters = lambda x: re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]', '', x)


CHMOD_OWNER_FILE = stat.S_IRUSR | stat.S_IWUSR
CHMOD_OWNER_DIR = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
CHMOD_ALL_FILE = CHMOD_OWNER_FILE | stat.S_IRGRP | stat.S_IROTH
CHMOD_ALL_DIR = CHMOD_OWNER_FILE | stat.S_IRGRP | stat.S_IROTH | stat.S_IXGRP | stat.S_IXOTH


def sanitize(name):
    # convert name to valid posix
    return name.replace('/', '-').replace(';', '-').replace('$', '-')


# load username to uid cache
UID_CACHE = {'root': 0, 'icecube': 0}
UID_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'username_uid_map.json')
if os.path.exists(UID_CACHE_PATH):
    with open(UID_CACHE_PATH) as f:
        UID_CACHE.update(json.load(f))
else:
    print('Loading usernames from LDAP', file=sys.stderr, end='\n')
    from ldap3 import Connection, ALL
    conn = Connection('ldap-1.icecube.wisc.edu', auto_bind=True)
    entries = conn.extend.standard.paged_search('ou=People,dc=icecube,dc=wisc,dc=edu', '(objectclass=posixAccount)', attributes=['uid', 'uidNumber'], paged_size=100)
    for entry in entries:
        attrs = entry['attributes']
        UID_CACHE[attrs['uid'][0]] = attrs['uidNumber']
    with open(UID_CACHE_PATH, 'w') as f:
        json.dump(UID_CACHE, f)


def total_seconds(timedelta):
    # because python 2.6 is super old, we do this directly instead of .total_seconds()
    return int(timedelta.seconds + timedelta.days * 24 * 3600)


IGNORE_DOC_TYPES = ('Group', 'BulletinBoard', 'Bulletin', 'Weblog', 'WeblogEntry', 'Event', 'Calendar', 'Wiki', 'WikiPage')


def get_documents(data, details=False):
    """Parse Docushare XML"""
    root = ET.fromstring(data.encode('utf-8'))

    collections = {}
    documents = {}
    users = {}

    for child in root:
        if 'classname' not in child.attrib:
            continue

        id_ = child.attrib['handle']
        type_ = child.attrib['classname']

        if type_ in ('Document', 'Collection', 'URL'):
            owner = None
            for obj in child.findall('./destinationlinks/owner'):
                owner = obj.text

            private = True
            acls = child.find('acls')
            for acl in acls:
                if acl.attrib['principal'] == 'Group-4':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
                if acl.attrib['principal'] == 'Group-5':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
                if acl.attrib['principal'] == 'Group-7':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
            
        if type_ == 'Document':
            if not details:
                documents[id_] = {
                    'type': 'Document',
                }
            else:
                props = child.find('props')
                title = id_
                original_file_name = None
                if props is not None and len(props) > 0:
                    for prop in props:
                        if prop.attrib['name'] == 'title':
                            title = prop.text.strip()
                        if prop.attrib['name'] == 'original_file_name':
                            original_file_name = prop.text.strip()

                versions = child.find('versions')
                if versions is None:
                    print(ET.tostring(child).decode('utf8'))
                    raise Exception('no versions')
                elif len(versions) == 1:
                    version_object = versions.findall('dsobject')[0]
                else:
                    for obj in child.findall('./destinationlinks/preferredVersion'):
                        # we have a preferred version, so use it
                        handle = obj.text
                        #print("preferred version handle", handle)
                        for v in versions.findall('dsobject'):
                            if v.attrib['handle'] == handle:
                                version_object = v
                                break
                        else:
                            raise Exception('no matching version')
                        break
                    else:
                        print(ET.tostring(child))
                        raise Exception('no preferredVersion')

                renditions = version_object.findall('./renditions/dsobject')
                if len(renditions) == 0:
                    print(ET.tostring(child))
                    raise Exception('no rendition')
                elif len(renditions) > 1:
                    print(ET.tostring(child))
                    raise Exception('too many renditions')
                size = -1
                date = None
                for r in renditions:
                    for prop in r.find('props'):
                        if prop.attrib['name'] == 'size':
                            size = prop.text
                        if prop.attrib['name'] == 'create_date':
                            date = total_seconds(datetime.strptime(prop.text, '%a %b %d %H:%M:%S %Z %Y') - datetime.fromtimestamp(0))
                    for o in r.findall('./contentelements/contentelement'):
                        filename = o.attrib['filename']
                        if title == id_:
                            title = o.text.strip()
                        break
                    else:
                        filename = None
                    break
                else:
                    size = -1
                    filename = None

                documents[id_] = {
                    'type': 'Document',
                    'owner': owner,
                    'title': title,
                    'private': private,
                    'size': size,
                    'filename': filename,
                    'original_file_name': original_file_name,
                    'date': date,
                }

        elif type_ == 'Collection':
            title = ''
            sort_order = 'Title'
            date = None
            props = child.find('props')
            if props is None or len(props) == 0:
                title = id_
            else:
                for prop in props:
                    if prop.attrib['name'] == 'title':
                        title = prop.text.strip()
                    if prop.attrib['name'] == 'sort_order':
                        sort_order = prop.text.strip()
                    if prop.attrib['name'] == 'create_date':
                        date = total_seconds(datetime.strptime(prop.text, '%a %b %d %H:%M:%S %Z %Y') - datetime.fromtimestamp(0))

            documents[id_] = {
                'type': 'Collection',
                'title': title,
                'sort_order': sort_order,
                'date': date,
                'owner': owner,
                'private': private,
                'children': [e.text for e in child.findall('./destinationlinks/containment')],
            }

        elif type_ == 'URL':
            if not details:
                documents[id_] = {
                    'type': 'URL',
                }
            else:
                title = ''
                url = ''
                props = child.find('props')
                if props is None or len(props) == 0:
                    title = id_
                else:
                    for prop in props:
                        if prop.attrib['name'] == 'title':
                            title = prop.text.strip()
                        if prop.attrib['name'] == 'url':
                            url = prop.text.strip()

                if not url:
                    continue

                documents[id_] = {
                    'type': 'URL',
                    'title': title,
                    'url': url,
                    'owner': owner,
                    'private': private,
                }

        elif type_ == 'User':
            props = child.find('props')
            if props is None:
                print(ET.tostring(child))
                raise Exception('no props')
            username = None
            for prop in props:
                if prop.attrib['name'] == 'username':
                    username = prop.text.strip()
                    break
            else:
                raise Exception('no username in user')

            documents[id_] = {
                'type': 'User',
                'username': username,
            }

        elif type_ in IGNORE_DOC_TYPES:
            documents[id_] = {
                'type': type_,
            }

        else:
            print(ET.tostring(child).decode('utf8'))
            raise Exception('new type')

        child.clear()

    return documents


class TreeNode(list):
    def __init__(self):
        super(TreeNode, self).__init__()
        self.parent = None


class Tree:
    def __init__(self, documents=None):
        self.nodes = {}
        self.roots = set()
        self.documents = documents

    def add_node(self, id_, parent=None, children=None):
        if id_ in self.nodes:
            node = self.nodes[id_]
        else:
            node = self.nodes[id_] = TreeNode()

        if parent is not None:
            node.parent = parent
            if id_ not in self.nodes[parent]:
                self.nodes[parent].append(id_)
            self.roots.discard(id_)
        elif node.parent is None:
            self.roots.add(id_)

        if children is not None:
            self.add_children(id_, children)

    def add_children(self, id_, children):
        node = self.nodes[id_]
        if len(node) > 0:
            for c in children:
                if c not in node:
                    node.append(c)
        else:
            node.extend(children)

    def set_parent(self, parent_id, child_id):
        if child_id not in self.nodes:
            self.add_node(child_id, parent=parent_id)
        else:
            self.add_children(parent_id, [child_id])
            self.nodes[child_id].parent = parent_id
            self.roots.discard(child_id)


class TreeSorts:
    @staticmethod
    def lookup(name):
        try:
            return getattr(TreeSorts, name)
        except AttributeError:
            return TreeSorts.Default

    @staticmethod
    def Title(tree, id_):
        def key(k):
            try:
                return (0, tree.documents[k]['title'])
            except KeyError:
                return (1,'')
        tree.nodes[id_].sort(key=key)

    @staticmethod
    def TitleReversed(tree, id_):
        def key(k):
            try:
                return (0, tree.documents[k]['title'])
            except KeyError:
                return (1,'')
        tree.nodes[id_].sort(key=key, reverse=True)

    TypeAndTitle = Title
    TypeAndTitleReversed = TitleReversed

    @staticmethod
    def Default(tree, id_):
        pass


def build_tree(documents):
    tree = Tree(documents)
    for id_ in documents:
        doc = documents[id_]
        if doc['type'] == 'Collection':
            tree.add_node(id_, children=doc['children'])
            for child in doc['children']:
                if child.startswith('Collection'):
                    tree.set_parent(id_, child)
    for id_ in documents:
        doc = documents[id_]
        if doc['type'] == 'Collection' and doc['children']:
            TreeSorts.lookup(doc['sort_order'])(tree, id_)
    return tree


class TreeWalker:
    def __init__(self, tree, skip_level=None, traversal_type='dfs'):
        self.tree = tree
        self.skip_level = skip_level
        self.traversal_type = traversal_type
        self.seen = set()

    def _traverse_dfs(self, id_, parents):
        level = len(parents)
        if self.skip_level and level >= self.skip_level:
            return

        self.seen.add(id_)
        yield id_, parents
        new_parents = parents + [id_]
        for d in self.tree.nodes[id_]:
            if d in self.tree.documents:
                doc = self.tree.documents[d]
            else:
                doc = {'type': 'Document'}
            if doc['type'] == 'Collection':
                if d in new_parents or d in self.seen:
                    # anti-loop code
                    continue
                for ret in self._traverse_helper(d, new_parents):
                    yield ret
            else:
                yield d, new_parents

    def traverse(self, id_=None):
        ids = [id_] if id_ else self.tree.roots
        
        if self.traversal_type == 'dfs':
            for id_ in ids:
                for ret in self._traverse_dfs(id_, []):
                    yield ret
        else: # bfs
            for id_ in ids:
                yield id_, []

            queue = [(id_, []) for id_ in ids]
            while queue:
                id_, parents = queue[0]
                queue = queue[1:]
                self.seen.add(id_)
                parents.append(id_)
                for d in self.tree.nodes[id_]:
                    if d in self.tree.documents:
                        doc = self.tree.documents[d]
                    else:
                        doc = {'type': 'Document'}
                    if doc['type'] == 'Collection':
                        if d in parents or d in self.seen:
                            # anti-loop code
                            continue
                        queue.append((d, list(parents)))
                    yield d, parents


def progress(total, iterator):
    last = time.time()
    for num, ret in enumerate(iterator):
        now = time.time()
        if now - last > 60:
            print(num/total)
            last = now
        yield ret


DOCUHIDE_PATH = '/root/docuhide/'


def dsexport(arg, recursive=False, metadata=False, props=None):
    cmd = './dsexport.sh -d '+DOCUHIDE_PATH+' '
    if recursive:
        cmd += '-r -t 8 '
    if metadata:
        cmd += '-m '
    if props:
        cmd += '-p '+','.join(props)+' '
    cmd += arg
    FNULL = open('/root/docuhide/export_err', 'w')
    subprocess.check_call(cmd, cwd='/root/docushare/bin', shell=True, stdout=FNULL, stderr=subprocess.STDOUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_xml', nargs='+', help='use input xml path for testing')
    parser.add_argument('--output', help='output directory (specify to get output)')
    parser.add_argument('--output-mapping', default='/dev/null', help='output mapping file (id,path)')
    parser.add_argument('--parallel', default=1, type=int, help='parallel document lookup for output')
    parser.add_argument('--max-depth', default=None, type=int, help='max depth of output tree')
    parser.add_argument('--sub-collection', default=None, help='sub-collection to run on')
    args = parser.parse_args()

    documents = {}
    if args.input_xml:
        for path in args.input_xml:
            with io.open(path, 'r', encoding='utf-8') as f:
                input_xml = escape_illegal_xml_characters(f.read())
                documents.update(get_documents(input_xml, details=True))
                del input_xml
    else:
        path = DOCUHIDE_PATH+'Collection/Collection.xml'
        if not os.path.exists(path):
            print('Running dsexport for Collection metadata', file=sys.stderr, end='\n')
            dsexport('Collection', metadata=True, props=['title', 'create_date', 'sort_order'])
        print('Processing Collection metadata', file=sys.stderr, end='\n')
        with io.open(path, 'r', encoding='utf-8') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

        path = DOCUHIDE_PATH+'Document/Document.xml'
        if not os.path.exists(path):
            print('Running dsexport for Document metadata', file=sys.stderr, end='\n')
            dsexport('Document', metadata=True, props=['noprops'])
        print('Processing Document metadata', file=sys.stderr, end='\n')
        with io.open(path, 'r', encoding='utf-8') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

        path = DOCUHIDE_PATH+'URL/URL.xml'
        if not io.os.path.exists(path):
            print('Running dsexport for URL metadata', file=sys.stderr, end='\n')
            dsexport('URL', metadata=True, props=['noprops'])
        print('Processing URL metadata', file=sys.stderr, end='\n')
        with io.open(path, 'r', encoding='utf-8') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

    print('Completed processing metadata. Building tree', file=sys.stderr, end='\n')

    tree = build_tree(documents)

    # print tree
    print('Outputting tree and documents', file=sys.stderr, end='\n')

    def parallel(iter_, n=args.parallel):
        batch = tuple(islice(iter_, n))
        while batch:
            doc_ids = []
            for id_, parents in batch:
                try:
                    doc = documents[id_]
                except KeyError:
                    doc = {'type': 'Document', 'title': '', 'owner': 'root', 'private': False}

                if doc['type'] in IGNORE_DOC_TYPES:
                    continue
                if doc['type'] != 'Collection' and args.output:
                    doc_ids.append(id_)

            if doc_ids:
                # we need to get the actual document
                id_ = doc_ids[0]
                path = os.path.join(DOCUHIDE_PATH, id_)
                dsexport(' '.join(doc_ids), recursive=True)
                with io.open(os.path.join(path, id_+'.xml'), 'r', encoding='utf-8') as f:
                    input_xml = escape_illegal_xml_characters(f.read())
                new_docs = get_documents(input_xml, details=True)
                del input_xml

            try:
                for id_, parents in batch:
                    try:
                        doc = documents[id_]
                    except KeyError:
                        doc = {'type': 'Document', 'title': '', 'owner': 'root', 'private': False}

                    base_path = None
                    if doc['type'] in IGNORE_DOC_TYPES:
                        continue
                    if doc['type'] != 'Collection' and args.output:
                        doc = new_docs[id_]
                        base_path = path

                    yield (id_, parents, doc, base_path)
            finally:
                if doc_ids:
                    # clean up
                    shutil.rmtree(path)
            batch = tuple(islice(iter_, n))

    wt = TreeWalker(tree, skip_level=args.max_depth, traversal_type='bfs')
    tree_iter = wt.traverse(id_=args.sub_collection)
    with io.open(args.output_mapping, 'w', 1) as mapping_file:
        for id_, parents, doc, base_path in progress(len(tree.nodes), parallel(tree_iter)):
            level = len(parents)
            if doc['type'] in IGNORE_DOC_TYPES:
                continue

            # print tree
            title = doc['title']
            private = doc['private']
            owner = doc['owner']
            user = documents.get(owner,{}).get('username','root')
            uid = UID_CACHE.get(user, 0)
            print('|'+'-'*level + id_, user, private)#, title, user, uid, private)

            # make posix output
            if args.output:
                if level == 0:
                    dir_path = args.output
                else:
                    dir_path = os.path.join(args.output, *[sanitize(documents[d]['title']) for d in parents])

                if doc['type'] == 'Collection':
                    dest_path = os.path.join(dir_path, sanitize(doc['title']))
                    if not os.path.exists(dest_path):
                        os.mkdir(dest_path)
                    if private:
                        perms = CHMOD_OWNER_DIR
                    else:
                        perms = CHMOD_ALL_DIR
                else:
                    if doc['type'] == 'URL':
                        dest_path = os.path.join(dir_path, sanitize(doc['title']))+'.txt'
                        with open(dest_path, 'w') as f:
                            print(doc['url'], file=f)
                    elif doc['type'] == 'Document':
                        src_path = os.path.join(base_path, 'documents', doc['filename'])
                        dest_path = os.path.join(dir_path, sanitize(doc['title']))
                        dest_ext = os.path.splitext(dest_path)[-1]
                        if (not dest_ext) or (len(dest_ext) > 5):
                            ext = os.path.splitext(doc['original_file_name'])[-1]
                            if not ext:
                                ext = os.path.splitext(doc['filename'])[-1]
                            if ext:
                                dest_path += ext
                        try:
                            shutil.copyfile(src_path, dest_path)
                        except Exception:
                            print('doc', doc, file=sys.stderr)
                            print('src_path', src_path, file=sys.stderr)
                            print('dest_path', dest_path, file=sys.stderr)
                            raise
                    else:
                        print('id:', id_, file=sys.stderr)
                        print('doc', doc, file=sys.stderr)
                        raise Exception('unknown doc type')

                    if private:
                        perms = CHMOD_OWNER_FILE
                    else:
                        perms = CHMOD_ALL_FILE

                if doc.get('date', None) is not None:
                    try:
                        os.utime(dest_path, (doc['date'], doc['date']))
                    except Exception:
                        print('cannot set time on', dest_path, file=sys.stderr)

                # set ownership and perms
                os.chown(dest_path, uid, uid)
                os.chmod(dest_path, perms)

                # output mapping
                mapping_file.write(id_ + ',' + dest_path + '\n')

    print('Export complete!', file=sys.stderr, end='\n')


if __name__ == '__main__':
    main()
