from lxml import etree
import uuid
import pm4py

def export_petri_to_pnml(petriNet, marking, outputFilePath):
    root = etree.Element("pnml")
    net = etree.SubElement(root, "net")
    net.set("id","net1")
    net.set("type","http://www.pnml.org/version-2009/grammar/pnmlcoremodel")
    page = etree.SubElement(net, "page")
    page.set("id","n0")
    placesMap = {}
    for place in petriNet.places:
        placesMap[place] = str(hash(place))
        pl = etree.SubElement(page, "place")
        pl.set("id", str(hash(place)))
        plName = etree.SubElement(pl,"name")
        plNameText = etree.SubElement(plName,"text")
        plNameText.text = place.name
        if place in marking:
            plInitialMarking = etree.SubElement(pl,"initialMarking")
            plInitialMarkingText = etree.SubElement(plInitialMarking,"text")
            plInitialMarkingText.text = str(marking[place])
    transitionsMap = {}
    for transition in petriNet.transitions:
        transitionsMap[transition] = str(hash(transition))
        trans = etree.SubElement(page, "transition")
        trans.set("id", str(hash(transition)))
        transName = etree.SubElement(trans, "name")
        transText = etree.SubElement(transName, "text")
        if transition.label is not None:
            transText.text = transition.label
        else:
            transText.text = transition.name
            toolSpecific = etree.SubElement(trans, "toolspecific")
            toolSpecific.set("tool", "ProM")
            toolSpecific.set("version", "6.4")
            toolSpecific.set("activity", "$invisible$")
            toolSpecific.set("localNodeID", str(uuid.uuid4()))
    for arc in petriNet.arcs:
        arcEl = etree.SubElement(page, "arc")
        arcEl.set("id", str(hash(arc)))
        if type(arc.source) is pm4py.models.petri.net.PetriNet.Place:
            arcEl.set("source", str(placesMap[arc.source]))
            arcEl.set("target", str(transitionsMap[arc.target]))
        else:
            arcEl.set("source", str(transitionsMap[arc.source]))
            arcEl.set("target", str(placesMap[arc.target]))




    tree = etree.ElementTree(root)
    tree.write(outputFilePath, pretty_print=True, xml_declaration=True, encoding="utf-8")