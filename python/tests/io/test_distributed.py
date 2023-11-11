import pytest

from mspasspy.db.database import Database
from mspasspy.db.client import DBClient
from helper import (
    get_live_seismogram,
    get_live_timeseries,
    get_live_timeseries_ensemble,
    get_live_seismogram_ensemble,
)
from bson.objectid import ObjectId
from datetime import datetime
import copy
import dask
from pyspark import SparkContext
sc = SparkContext("local", "io_distributed_testing")

from mspasspy.io.distributed import (
    read_distributed_data,
    write_distributed_data,
    read_to_dataframe,
)
from mspasspy.db.normalize import ObjectIdMatcher

def make_channel_record(val,net="00",sta="sta",chan="chan",loc="00"):
    """
    Returns a dict of base attributes needed for default of 
    ObjectIdMatcher for the channel collection.   The value of 
    all numeric fields are set to input parameter val value.  
    net, sta, chan, and loc are defaulted but can be changed with kwargs 
    values if needed.   Having constant values is appropriate for this 
    test file but not for real data.
    """
    doc=dict()
    doc['sta']=sta
    doc['net']=net
    doc['chan']=chan
    doc['loc']=loc
    doc['lat']=val
    doc['lon']=val
    doc['elev']=val
    doc['starttime']=0.0   # 0 epoch time for universal match
    doc['endtime']=datetime.utcnow().timestamp()
    return doc

def make_site_record(val,net="00",sta="sta",loc="00"):
    """
    Returns a dict of base attributes needed for default of 
    ObjectIdMatcher for the site collection.   The value of 
    all numeric fields are set to input parameter val value.  
    net, sta,  and loc are defaulted but can be changed with kwargs 
    values if needed.   Having constant values is appropriate for this 
    test file but not for real data.
    """
    doc=dict()
    doc['sta']=sta
    doc['net']=net
    doc['loc']=loc
    doc['lat']=val
    doc['lon']=val
    doc['elev']=val
    doc['starttime']=0.0   # 0 epoch time for universal match
    doc['endtime']=datetime.utcnow().timestamp()
    return doc

def make_source_record(val,time=0.0):
    """
    Returns a dict of base attributes needed for default of 
    ObjectIdMatcher for the source collection.   The value of 
    all numeric fields are set to input parameter val value.  
    Having constant values is appropriate for this 
    test file but not for real data.
    
    time can be changed if desired for each entry to make something unique
    for each source
    """
    doc=dict()

    doc['lat']=val
    doc['lon']=val
    doc['depth']=val
    doc['time']=time
    doc['magnitude']=1.0
    return doc
# globals for this test module
number_atomic_wf = 3
number_ensemble_wf = 4   # intentionaly not same as number_atomic_wf
number_ensembles = 4   # ensemble tests create this many ensembles 
testdbname = "mspass_test_db"


@pytest.fixture
def atomic_time_series_generator():
    """
    Regularizes creation of wf_TimeSeries and stock normalizing 
    collection entries for TimeSeries tests with atomic read/write.  
    Generates 3 copies with different channel_id values and 
    a single source_id linking record.  
    
   Note online sources show complicated ways that might be useful 
   here to allow some things like number of items generated to 
   be a kwargs value.   For now I (glp) will leave that as a 
   simple constant set within this script as a global.
    
    Returns a list of ObjectIDs of waveforms saved to wf_TimeSeries. 
    Note each datum has linking, valid id links to source, site, 
    and channel.

    """

    
    client = DBClient("localhost")
    db = client.get_database(testdbname)

    source_doc = make_source_record(1.0)
    source_id = db.source.insert_one(source_doc).inserted_id
    wfid_list=[]
    for i in range(number_atomic_wf):
        sta="station{}".format(i)
        channel_doc = make_channel_record(float(i),sta=sta)
        channel_id = db.channel.insert_one(channel_doc).inserted_id
        site_doc = make_site_record(float(i),sta=sta)
        site_id = db.site.insert_one(site_doc).inserted_id
        test_ts = get_live_timeseries()
        test_ts["site_id"] = site_id
        test_ts["source_id"] = source_id
        test_ts["channel_id"] = channel_id
        sdret = db.save_data(test_ts,collection="wf_TimeSeries")
        # default for save_data returns a dict with _id defined. 
        # cpi;d break if the default changes
        wfid_list.append(sdret['_id'])
    yield wfid_list
    # this is cleanup code when test using this fixture exits
    # drop_database does almost nothng if name doesn't exist so 
    # we don't worry about multiple fixtures calling it
    client.drop_database(testdbname)


@pytest.fixture
def atomic_seismogram_generator():
    """
    Regularizes creation of wf_Seismogram and stock normalizing 
    collection entries for Seismogram tests with atomic read/write.  
    Generates 3 copies with different channel_id values and 
    a single source_id linking record.  
    
    Note online sources show complicated ways that might be useful 
    here to allow some things like number of items generated to 
    be a kwargs value.   For now I (glp) will leave that as a 
    simple constant set within this script as a global.
    
    Returns a list of ObjectIDs of waveforms saved to wf_Seismogram. 
    Note each datum has linking, valid id links to source, site, 
    and channel.

    """

    
    client = DBClient("localhost")
    db = client.get_database(testdbname)

    source_doc = make_source_record(1.0)
    source_id = db.source.insert_one(source_doc).inserted_id
    wfid_list=[]
    for i in range(number_atomic_wf):
        sta="station{}".format(i)
        site_doc = make_site_record(float(i),sta=sta)
        site_id = db.site.insert_one(site_doc).inserted_id
        test_ts = get_live_seismogram()
        test_ts["site_id"] = site_id
        test_ts["source_id"] = source_id
        sdret = db.save_data(test_ts,collection="wf_Seismogram")
        # default for save_data returns a dict with _id defined. 
        # cpi;d break if the default changes
        wfid_list.append(sdret['_id'])
    yield wfid_list
    # this is cleanup code when test using this fixture exits
    # drop_database does almost nothng if name doesn't exist so 
    # we don't worry about multiple fixtures calling it
    client.drop_database(testdbname)
    
@pytest.fixture
def TimeSeriesEnsemble_generator():
    client = DBClient("localhost")
    db = client.get_database(testdbname)
    
    # create multiple ensembles with different source_ids 
    # note source_id is only put in ensemble metadata container 
    # to test writers handle copying it to all members on save
    # add one site record per atomic datum for simplicity
    wfids = []   # will contain a list of ObjectId lists
    for i in range(number_ensembles):
        e = get_live_timeseries_ensemble(number_ensemble_wf)
        source_doc = make_source_record(float(i))
        source_id = db.source.insert_one(source_doc).inserted_id
        e["source_id"]=source_id
        # give sta value a name that can be used to infer ensemble number 
        # and member number
        for j in range(len(e.member)):
            sta="sta_{}_{}".format(i,j)
            site_doc = make_site_record(float(i+j),sta=sta)
            site_id = db.site.insert_one(site_doc).inserted_id
            e.member[j]["sta"] = sta
            e.member[j]["site_id"] = site_id
        e = db.save_data(e,collection="wf_TimeSeries",return_data=True)
        e_wfids = []
        for d in e.member:
            e_wfids.append(d['_id'])
        wfids.append(e_wfids)
    yield wfids
    # this is cleanup code when test using this fixture exits
    # drop_database does almost nothng if name doesn't exist so 
    # we don't worry about multiple fixtures calling it
    client.drop_database(testdbname)

@pytest.fixture
def SeismogramEnsemble_generator():
    pass

        
@pytest.mark.parametrize("format,collection", 
                         #[("spark","wf_TimeSeries")])
                         [("dask","wf_TimeSeries"),
                          ("dask","wf_Seismogram"),
                          ("spark","wf_TimeSeries"),
                          ("spark","wf_Seismogram")
                          ])
def test_read_distributed_atomic(atomic_time_series_generator,atomic_seismogram_generator,format,collection):
    """
    This function is run with multiple tests to test atomic read (mostly) and 
    limited writes with the io.distributed module. That is, read_distributed_data 
    and its inverse write_distributed_data.   Although it perhaps belongs 
    elsewhere it also tests the standalone function read_to_dataframe.  
    What it tests is controlled by the two input parameters "format" and 
    "collection".   format must be either "dask" or "spark" and 
    collection must be either "wf_TimeSeries" or "wf_Seismgoram" with the 
    default MsPASS schema.   Which combinations are tested are controlled 
    by the pytest decorator (mark_parameterize) with the incantation 
    found in the arg list for the decorator.  See numerous online sources 
    if you are (appropriately) confused by the syntax.  
    
    This function does only limited tests of the writer.   Exercising 
    additional features of the writer are found in test_write_distributed_atomic 
    and test_write_distributed_ensemble. 
    
    The function uses two fixtures that create and tear down a test database.
    Current use creates an initial set of data stored on gridfs with no 
    data_tag value.  Writer instances should each create a separate 
    data_tag for each all if additional test sections are added.  
    
    Normalization tests use new (v2+) feature of list of BasicMatcher
    subclasses.
    """
    print("Starting test with format=",format, " and collection=",collection)
    if format=="spark":
        context=sc
    else:
        context=None
    number_partitions=2
    if collection=="wf_TimeSeries":
        wfid_list = atomic_time_series_generator
    elif collection=="wf_Seismogram":
        wfid_list = atomic_seismogram_generator
    client = DBClient("localhost")
    db = client.get_database(testdbname)
    # first test reader without normalization.  Save result with 
    # data tag used to read copy back below
    bag_or_rdd = read_distributed_data(db,
                                  collection=collection,
                                  format=format,
                                  spark_context=context,
                                  )
    if format=="dask":
        wfdata_list=bag_or_rdd.compute()
    elif format=="spark":
        wfdata_list=bag_or_rdd.collect()
    else:
        raise ValueError("Illegal value format=",format)
    assert len(wfdata_list)==number_atomic_wf
    for d in wfdata_list:
        wfid = d["_id"]
        assert wfid in wfid_list
    wfdata_list.clear()
    nrmlist=[]
    # repeat with normalization - use channel only for TimeSeries
    if collection=="wf_TimeSeries":
        channel_matcher = ObjectIdMatcher(db,
                            "channel",
                            attributes_to_load=['net','sta','lat','lon','elev','_id'])
        nrmlist.append(channel_matcher)
    source_matcher = ObjectIdMatcher(db,
                        "source",
                        attributes_to_load=['lat','lon','depth','time','_id'])
    nrmlist.append(source_matcher)
    
    site_matcher = ObjectIdMatcher(db,
                        "site",
                        attributes_to_load=['net','sta','lat','lon','elev','_id'])
    nrmlist.append(site_matcher)
    bag_or_rdd = read_distributed_data(db,
                                  collection=collection,
                                  format=format,
                                  normalize=nrmlist,
                                  spark_context=context,
                                  )
    if format=="dask":
        wfdata_list=bag_or_rdd.compute()
    elif format=="spark":
        wfdata_list=bag_or_rdd.collect()
    else:
        raise ValueError("Illegal value format=",format)
    
    assert len(wfdata_list)==number_atomic_wf
    for d in wfdata_list:
        wfid = d["_id"]
        assert wfid in wfid_list
        # only verify one of each collection was set
        # intentionally don't test values as that could change in fixture
        assert d.is_defined("source_lat")
        if collection=="wf_TimeSeries":
            assert d.is_defined("channel_sta")
        assert d.is_defined("site_net")
    wfdata_list.clear()
    # repeat but this time push the bag into the writer and verify it works
    bag_or_rdd = read_distributed_data(db,
                                  collection=collection,
                                  format=format,
                                  normalize=nrmlist,
                                  spark_context=context,
                                  )
    new_wfids = write_distributed_data(bag_or_rdd, 
                                   db,
                                   data_are_atomic=True,
                                   collection=collection,
                                   data_tag="save_number_1",
                                   format=format,
                                   )
    # Default above is assumed for return_data == False.  In that 
    # case write_distributed_data should return a list of ObjectIds
    # of the saved waveforms.  We test that in the assertions below
    assert len(new_wfids)==number_atomic_wf
    # write_distributed_data only returns a list of ObjectId written
    # unless we set return_data True.
    # verify the ids are valid - i.e. were saved
    n = db[collection].count_documents({"data_tag" : "save_number_1"})
    assert n == number_atomic_wf
    cursor=db[collection].find({"data_tag" : "save_number_1"})
    for doc in cursor:
        testid=doc['_id']
        assert testid in new_wfids
    # nake sure this works with data_tag
    bag_or_rdd = read_distributed_data(db,
                                  collection=collection,
                                  format=format,
                                  normalize=nrmlist,
                                  data_tag="save_number_1",
                                  spark_context=context,
                                  )
    if format=="dask":
        wfdata_list=bag_or_rdd.compute()
    elif format=="spark":
        wfdata_list=bag_or_rdd.collect()
    else:
        raise ValueError("Illegal value format=",format)
    assert len(wfdata_list)==number_atomic_wf
    # test basic dataframe input - dataframe converter features are tested 
    # in a different function
    cursor.rewind()
    df = read_to_dataframe(db,cursor)
    bag_or_rdd = read_distributed_data(df,
                                  db,
                                  collection=collection,
                                  format=format,
                                  normalize=nrmlist,
                                  data_tag="save_number_1",
                                  npartitions=number_partitions,
                                  spark_context=context,
                                  )
    if format=="dask":
        wfdata_list=bag_or_rdd.compute()
    elif format=="spark":
        wfdata_list=bag_or_rdd.collect()
    else:
        raise ValueError("Illegal value format=",format)
    # note this dependency on above settign new_wfids - watch out if editing
    assert len(wfdata_list)==number_atomic_wf
    for d in wfdata_list:
        wfid = d["_id"]
        assert wfid in new_wfids
        # only verify one of each collection was set
        # intentionally don't test values as that could change in fixture
        assert d.is_defined("source_lat")
        if collection=="wf_TimeSeries":
            assert d.is_defined("channel_sta")
        assert d.is_defined("site_net")
    wfdata_list.clear()
    # test container_to_merge feature
    source_id = ObjectId()
    x={"merged_source_id" : source_id}
    xlist=[]
    for i in range(number_atomic_wf):
        xlist.append(x)
    if format=="dask":
        merge_bag = dask.bag.from_sequence(xlist,npartitions=number_partitions)
    else:
        # unclear if specifying number of parittions is required with spark
        # with dask they must match for the merge process to work
        merge_bag = context.parallelize(xlist,numSlices=number_partitions)
    bag_or_rdd = read_distributed_data(df,
                                  db,
                                  collection=collection,
                                  format=format,
                                  normalize=nrmlist,
                                  container_to_merge=merge_bag,
                                  npartitions=number_partitions,
                                  data_tag="save_number_1",
                                  spark_context=context,
                                  )
    if format=="dask":
        wfdata_list=bag_or_rdd.compute()
    elif format=="spark":
        wfdata_list=bag_or_rdd.collect()
    else:
        raise ValueError("Illegal value format=",format)
    assert len(wfdata_list)==number_atomic_wf
    for d in wfdata_list:
        assert d.is_defined("merged_source_id")
        assert d["merged_source_id"] == source_id
    #client.drop_database(testdbname)
        
@pytest.mark.parametrize("format,collection", 
                         [("dask","wf_TimeSeries")],
                          )
 #                         ("dask","wf_Seismogram"),
 #                         ("spark","wf_TimeSeries"),
 #                         ("spark","wf_Seismogram")
 #                         ])
def test_read_distributed_ensemble(TimeSeriesEnsemble_generator,SeismogramEnsemble_generator,format,collection):
     print("Starting test with format=",format, " and collection=",collection)
     if format=="spark":
         context=sc
     else:
         context=None
     number_partitions=2
     if collection=="wf_TimeSeries":
         wfid_list = TimeSeriesEnsemble_generator
     elif collection=="wf_Seismogram":
         wfid_list = SeismogramEnsemble_generator
     client = DBClient("localhost")
     db = client.get_database(testdbname)
     # We use source_id in this test to define ensembles.  
     # Used to generate a list of query dictionaries by source_id
     srcid_list=db.source.distince('_id')
     querylist=[]
     for srcid in srcid_list:
         querylist.append({'source_id' : srcid})
     # first test dask reader without normalization.  Save result with 
     # data tag used to read copy back below
     bag_or_rdd = read_distributed_data(querylist,
                                   db,
                                   collection=collection,
                                   format=format,
                                   spark_context=context,
                                   )
     if format=="dask":
         wfdata_list=bag_or_rdd.compute()
     elif format=="spark":
         wfdata_list=bag_or_rdd.collect()
    
     assert len(wfdata_list) == number_ensembles
     for e in wfdata_list:
         assert e.live
         assert len(e.member) == number_ensemble_wf
         for d in e.member:
             assert d.live
             # appropriate only because we read in default promiscuous mode
             assert d.is_defined("sta")
             
     # repeat with normalization to load source info in ensemble metadata
     # and site data into members
     nrmlist=[]
     source_matcher = ObjectIdMatcher(db,
                        "source",
                        attributes_to_load=['lat','lon','depth','time','_id'])
     nrmlist.append(source_matcher)
    
     site_matcher = ObjectIdMatcher(db,
                        "site",
                        attributes_to_load=['net','sta','lat','lon','elev','_id'])
     nrmlist.append(site_matcher)
     bag_or_rdd = read_distributed_data(querylist,
                                   db,
                                   collection=collection,
                                   format=format,
                                   spark_context=context,
                                   normalize=nrmlist,
                                   ensemble_metadata_list=['source_id'],
                                   )
     if format=="dask":
         wfdata_list=bag_or_rdd.compute()
     elif format=="spark":
         wfdata_list=bag_or_rdd.collect()
     
     assert len(wfdata_list) == number_ensembles
     for e in wfdata_list:
         assert e.live
         assert len(e.member) == number_ensemble_wf
         assert e.is_defined('source_id')
         assert e.is_defined('source_lat')
         for d in e.member:
             assert d.live
             # appropriate only because we read in default promiscuous mode
             assert d.is_defined("sta")
             assert d.is_defined("site_id")
             assert d.is_defined("site_lat")
             
     # now test the same read followed by a basic write
     bag_or_rdd = read_distributed_data(querylist,
                                  db,
                                  collection=collection,
                                  format=format,
                                  spark_context=context,
                                  normalize=nrmlist,
                                  ensemble_metadata_list=['source_id'],
                                  )
     data_tag = "save_number_1"   # made a variable to allow changes to copy code
     wfidlists = write_distributed_data(bag_or_rdd, 
                                    db,
                                    data_are_atomic=False,
                                    collection=collection,
                                    data_tag="save_number_1",
                                    format=format,
                                    )
     # check - may be wrong
     assert len(wfidlists)==number_ensemble_wf
         
     # default return examined here is a list of ObjectId lists
     # here we verify they match

     for srcid in srcid_list:
         query={"source_id" : srcid, "data_tag" : data_tag}
         assert db[collection].count_documents(query) == number_ensemble_wf
         cursor = db[collection].find(query)
         for doc in cursor:
             assert 'sta' in doc
             assert 'source_id' in doc
             assert doc['source_id']==srcid
             assert 'site_id' in doc
             # these should have been dropped on a save as normalization attributes
             assert 'site_lat' not in doc
             assert 'source_lat' not in doc
             assert doc['data_tag']==data_tag
             # this test is a bit more complex that might be expected
             # necessary because order is not guaranteed in the id lists
             number_hits=0
             for wfl in wfidlists:
                 wfid = doc['_id']
                 if wfid in wfl:
                     number_hits += 1
             assert number_hits==1
                 
             
             
     

    
def test_read_error_handlers(atomic_time_series_generator):
    atomic_time_series_generator
    client = DBClient("localhost")
    db = client.get_database(testdbname)
    
    # now test error handlers.  First, test reade error handlers
    # illegal value for format argument
    with pytest.raises(ValueError, match="Unsupported value for format"):
        mybag = read_distributed_data(db,
                                      collection="wf_TimeSeries",
                                      format="illegal_format",
                                      data_tag="save_number_1",
                                      )
    # illegal value for db argument when using dataframe input
    cursor=db.wf_TimeSeries.find({})
    df = read_to_dataframe(db,cursor)
    with pytest.raises(TypeError,match="Illegal type"):
        mybag = read_distributed_data(df,
                                      db=True,
                                      collection="wf_TimeSeries",
                                      format="dask",
                                      data_tag="save_number_1",
                                      )
    # Defaulted (none) value for db with dataframe input produces a differnt exeception
    with pytest.raises(TypeError,match="An instance of Database class is required"):
        mybag = read_distributed_data(df,
                                      db=None,
                                      collection="wf_TimeSeries",
                                      format="dask",
                                      data_tag="save_number_1",
                                      )
    # this is illegal input for arg0 test - message match is a bit cryptic
    with pytest.raises(TypeError,match="Must be a"):
        mybag = read_distributed_data(float(2),
                                      collection="wf_TimeSeries",
                                      format="dask",
                                      data_tag="save_number_1",
                                      )

        
@pytest.mark.parametrize("format,collection", 
                         [("dask","wf_TimeSeries"),
                          ("dask","wf_Seismogram"),
                          ("spark","wf_TimeSeries"),
                          ("spark","wf_Seismogram")
                          ])
def test_read_distributed_ensemble(TimeSeriesEnsemble_generator,SeismogramEnsemble_generator,format,collection):
    """
    Tests features of write_distributed_data for atomic dara.  
    """
    pass

@pytest.mark.parametrize("format,collection", 
                         [("dask","wf_TimeSeries"),
                          ("dask","wf_Seismogram"),
                          ("spark","wf_TimeSeries"),
                          ("spark","wf_Seismogram")
                          ])
def test_write_distributed_atomic(atomic_time_series_generator,atomic_seismogram_generator,format,collection):
    """
    Tests features of write_distributed_data for atomic dara.  
    """
    pass

@pytest.mark.parametrize("format,collection", 
                         [("dask","wf_TimeSeries"),
                          ("dask","wf_Seismogram"),
                          ("spark","wf_TimeSeries"),
                          ("spark","wf_Seismogram")
                          ])
def test_write_distributed_ensemble(TimeSeriesEnsemble_generator,SeismogramEnsemble_generator,format,collection):
    """
    Tests features of write_distributed_data for atomic dara.  
    """
    pass

    