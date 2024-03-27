from __future__ import print_function

import argparse
import xml.etree.ElementTree as ET
import re
import subprocess
import sys

escape_illegal_xml_characters = lambda x: re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]', '', x)


def get_documents(data):
    """Prints name, size, filename"""
    root = ET.fromstring(data)
    #root = tree.getroot()

    collections = {}
    documents = {}

    for child in root:
        if 'classname' not in child.attrib:
            continue

        type_ = child.attrib['classname']
            
        if type_ == 'Document':
            sourcelinks = child.find('sourcelinks')
            if sourcelinks is not None:
                parent = sourcelinks.find('containment').text
            else:
                parent = None
            props = child.find('props')
            if props is None:
                print(ET.tostring(child))
                raise Exception('no props')
            for prop in props:
                if prop.attrib['name'] == 'title':
                    title = prop.text
                    break
            else:
                raise Exception('no title in document')

            versions = child.find('versions')
            if versions is None:
                print(ET.tostring(child))
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
            for r in renditions:
                for prop in r.find('props'):
                    if prop.attrib['name'] == 'size':
                        size = prop.text
                        break
                else:
                    size = -1
                for o in r.findall('./contentelements/contentelement'):
                    filename = o.attrib['filename']
                    break
                else:
                    filename = None
                break
            else:
                size = -1
                filename = None

            documents[child.attrib['handle']] = {
                'parent': parent,
                'title': title,
                'size': size,
                'filename': filename,
            }

        elif type_ == 'Collection':
            sourcelinks = child.findall('./sourcelinks/containment')
            if len(sourcelinks) > 0:
                parent = sourcelinks[0].text
            else:
                parent = None
            props = child.find('props')
            if props is None:
                continue
            for prop in props:
                if prop.attrib['name'] == 'title':
                    title = prop.text
                    break
            else:
                raise Exception('no collection title')
            collections[child.attrib['handle']] = {
                'parent': parent,
                'title': title,
                'children': [e.text for e in child.findall('./destinationlinks/containment')],
            }

        elif type_ == 'URL':
            sourcelinks = child.find('sourcelinks')
            if sourcelinks is not None:
                parent = sourcelinks.find('containment').text
            else:
                parent = None
            props = child.find('props')
            if props is None:
                print(ET.tostring(child))
                raise Exception('no props')
            for prop in props:
                if prop.attrib['name'] == 'title':
                    title = prop.text
                    break
            else:
                raise Exception('no title in document')
            for prop in props:
                if prop.attrib['name'] == 'url':
                    url = prop.text
                    break
            else:
                raise Exception('no url in document')

            documents[child.attrib['handle']] = {
                'parent': parent,
                'title': title,
                'size': -1,
                'filename': '',
                'url': url,
            }

    return collections, documents


def walk_tree(collections, root=None, level=0):
    if not root:
        for c in collections:
            collection = collections[c]
            if not collection['parent']:
                root = c
                break
        else:
            raise Exception('cannot find root collection')

    yield root, level
    c = collections[root]
    for d in c['children']:
        if d.startswith('Collection'):
            for obj in walk_tree(collections, root=d, level=level+1):
                yield obj
        else:
            yield d, level+1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_xml', help='use input xml path for testing')
    parser.add_argument('--collection_id', help='Collection ID')
    parser.add_argument('--output', help='output directory (specify to get output)')
    args = parser.parse_args()

    if args.input_xml:
        with open(args.input_xml, 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
    elif args.collection_id:
        subprocess.check_call('./dsexport.sh -d /root/ -r -m '+args.collection_id, cwd='/root/docushare/bin', shell=True)
        with open('/root/'+args.collection_id+'/'+args.collection_id+'.xml', 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
    else:
        raise Exception('must specify either --input_xml or --collection_id')

    collections, documents = get_documents(input_xml)
    del input_xml

    for id_, level in walk_tree(collections):
        if id_.startswith('Collection'):
            title = collections[id_]['title']
        else:
            title = documents[id_]['title']
        print(' '*level, id_, title)

if __name__ == '__main__':
    main()
