import xml.etree.ElementTree as ET
import re
import sys


escape_illegal_xml_characters = lambda x: re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]', '', x)


def get_documents(path):
    """Prints name, size, filename"""
    with open(path, 'r') as f:
        data = escape_illegal_xml_characters(f.read())
    root = ET.fromstring(data)
    #root = tree.getroot()

    collections = {}
    documents = {}

    for child in root:
        if 'classname' not in child.attrib:
            continue
        if child.attrib['classname'] == 'Document':
            sourcelinks = child.find('sourcelinks')
            if sourcelinks:
                parent = sourcelinks.find('containment').text
            else:
                parent = None
            props = child.find('props')
            if not props:
                continue
            for prop in props:
                if prop.attrib['name'] == 'title':
                    title = prop.text
                    break
            else:
                raise Exception('no title in document')

            versions = child.find('versions')
            if not versions:
                continue
            for v in versions.findall('dsobject'):
                renditions = v.find('renditions')
                if not renditions:
                    continue
                for r in renditions.findall('dsobject'):
                    for prop in r.find('props'):
                        if prop.attrib['name'] == 'size':
                            size = prop.text
                            break
                    else:
                        size = -1
                    for o in r.findall('./contentelements.contentelement'):
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
                    
        elif child.attrib['classname'] == 'Collection':
            sourcelinks = child.find('sourcelinks')
            if sourcelinks:
                parent = sourcelinks.find('containment').text
            else:
                parent = None
            props = child.find('props')
            if not props:
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
            yield from walk_tree(collections, root=d, level=level+1)
        else:
            yield d, level+1


def main():
    collections, documents = get_documents(sys.argv[1])

    for id_, level in walk_tree(collections):
        print(' '*level, id_)

if __name__ == '__main__':
    main()