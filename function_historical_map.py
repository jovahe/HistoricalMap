"""!@brief Interface between qgisForm and function_historical_map.py
./***************************************************************************
 HistoricalMap
                                 A QGIS plugin
 Mapping old landcover (specially forest) from historical  maps
                              -------------------
        begin                : 2016-01-26
        git sha              : $Format:%H$
        copyright            : (C) 2016 by Karasiak & Lomellini
        email                : karasiak.nicolas@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
# -*- coding: utf-8 -*-
import function_dataraster as dataraster
import pickle
import os
import accuracy_index as ai
import tempfile
import gmm_ridge as gmmr
import scipy as sp
from scipy import ndimage
from osgeo import gdal, ogr, osr
from PyQt4.QtGui import QProgressBar, QApplication
from PyQt4 import QtCore
from qgis.utils import iface
from qgis.core import QgsMessageLog

class historicalFilter():  
    """!@brief Filter a raster with median and closing filter.
    
    Filter class to isolate the forest, delete dark lines and fonts from Historical Map
    
    Input :
        inImage : image name to filter ('text.tif',str)
        outName : outname name of the filtered file (str)
        inShapeGrey : Size for the grey closing convolution matrix (odd number, int)
        inShapeMedian : Size for the median convolution matrix (odd  number, int)
        
    Output :
        Nothing except a raster file (outName)
        
    """
    
    def __init__(self,inImage,outName,inShapeGrey,inShapeMedian,iterMedian):
        # open data with Gdal
        try:
            data,im=dataraster.open_data_band(inImage)
        except:
            print 'Cannot open image'

        # get proj,geo and dimension (d) from data
        proj = data.GetProjection()
        geo = data.GetGeoTransform()
        d = data.RasterCount
        
        # Progress Bar
        maxStep=d+d*iterMedian
        try:
            filterProgress=progressBar(' Filtering...',maxStep)
        except:
            print 'Failed loading progress Bar'
            
        # create empty geotiff with d dimension, geotransform & projection
        try:
            outFile=dataraster.create_empty_tiff(outName,im,d,geo,proj)
        except:
            print 'Cannot write empty image '+outName
        
        # fill outFile with filtered band
        for i in range(d):
            # Read data from the right band
            try:
                filterProgress.addStep()
                temp = data.GetRasterBand(i+1).ReadAsArray()
                
            except:
                print 'Cannot get rasterband'+i
                QgsMessageLog.logMessage("Problem reading band "+str(i)+" from image "+inImage)
            # Filter with greyclosing, then with median filter
            try:
                temp = ndimage.morphology.grey_closing(temp,size=(inShapeGrey,inShapeGrey))
            except:
                print 'Cannot filter with Grey_Closing'
                QgsMessageLog.logMessage("Problem with Grey Closing")

            for j in range(iterMedian):
                try:
                    filterProgress.addStep()
                    temp = ndimage.filters.median_filter(temp,size=(inShapeMedian,inShapeMedian))
                except:
                    print 'Cannot filter with Median'
                    QgsMessageLog.logMessage("Problem with median filter")
                
            # Save bandand outFile
            try:
                out=outFile.GetRasterBand(i+1)
                out.WriteArray(temp)
                out.FlushCache()
                temp = None
            except:
                QgsMessageLog.logMessage("Cannot save band"+str(i)+" on image" + outName)
                
        filterProgress.reset()
                
class learnModel():
    """!@brief Learn model with a shp file and a raster image.
    
    Input :
        inRaster : Filtered image name ('sample_filtered.tif',str).
        inVector : Name of the training shpfile ('training.shp',str).
        inField : Column name where are stored class number (str).
        inSplit : (int).
        inSeed : (int).
        outModel : Name of the model to save, will be compulsory for the 3rd step (classifying).
        outMatrix : Default the name of the file inRaster(minus the extension)_inClassifier_inSeed_confu.csv (str).
        inClassifier : GMM,KNN,SVM, or RF. (str).
        
    Output :
        Model file.
        Confusion Matrix.
        
    """
    def __init__(self,inRaster,inVector,inField='Class',inSplit=0.5,inSeed=0,outModel=None,outMatrix=None,inClassifier='GMM'):
          
          
        learningProgress=progressBar('Learning model...',6)
 
        # Convert vector to raster

        try:
            temp_folder = tempfile.mkdtemp()
            filename = os.path.join(temp_folder, 'temp.tif')
            
            data = gdal.Open(inRaster,gdal.GA_ReadOnly)
            shp = ogr.Open(inVector)
            
            lyr = shp.GetLayer()
        except:
            QgsMessageLog.logMessage("Problem with making tempfile or opening raster or vector")
        
        # Create temporary data set
        try:
            driver = gdal.GetDriverByName('GTiff')
            dst_ds = driver.Create(filename,data.RasterXSize,data.RasterYSize, 1,gdal.GDT_Byte)
            dst_ds.SetGeoTransform(data.GetGeoTransform())
            dst_ds.SetProjection(data.GetProjection())
            OPTIONS = 'ATTRIBUTE='+inField
            gdal.RasterizeLayer(dst_ds, [1], lyr, None,options=[OPTIONS])
            data,dst_ds,shp,lyr=None,None,None,None
        except:
            QgsMessageLog.logMessage("Cannot create temporary data set")
        
        # Load Training set
        try:
            X,Y =  dataraster.get_samples_from_roi(inRaster,filename)
        except:
            QgsMessageLog.logMessage("Problem while getting samples from ROI with"+inRaster)
        
        [n,d] = X.shape
        C = int(Y.max())
        SPLIT = inSplit
        os.remove(filename)
        os.rmdir(temp_folder)
        
        # Scale the data
        X,M,m = self.scale(X)
        
        
        learningProgress.addStep() # Add Step to ProgressBar

        # Learning process take split of groundthruth pixels for training and the remaining for testing
        try:
            if SPLIT < 1:
                # progressBar, set Max to C
                
                # Random selection of the sample
                x = sp.array([]).reshape(0,d)
                y = sp.array([]).reshape(0,1)
                xt = sp.array([]).reshape(0,d)
                yt = sp.array([]).reshape(0,1)
                
                sp.random.seed(inSeed) # Set the random generator state
                for i in range(C):            
                    t = sp.where((i+1)==Y)[0]
                    nc = t.size
                    ns = int(nc*SPLIT)
                    rp =  sp.random.permutation(nc)
                    x = sp.concatenate((X[t[rp[0:ns]],:],x))
                    xt = sp.concatenate((X[t[rp[ns:]],:],xt))
                    y = sp.concatenate((Y[t[rp[0:ns]]],y))
                    yt = sp.concatenate((Y[t[rp[ns:]]],yt))
                    #Add Pb
        except:
            QgsMessageLog.logMessage("Problem while learning if SPLIT <1")
                
        else:
            x,y=X,Y
        
        learningProgress.addStep() # Add Step to ProgressBar
        # Train Classifier
        try:
            if inClassifier == 'GMM':
                # tau=10.0**sp.arange(-8,8,0.5)
                model = gmmr.GMMR()
                model.learn(x,y)
                # htau,err = model.cross_validation(x,y,tau)
                # model.tau = htau
        except:
            QgsMessageLog.logMessage("Cannot train with GMMM")
        else:
            try:                    
                from sklearn import neighbors
                from sklearn.svm import SVC
                from sklearn.ensemble import RandomForestClassifier
                from sklearn.cross_validation import StratifiedKFold
                from sklearn.grid_search import GridSearchCV
            except:
                QgsMessageLog.logMessage("You must have sklearn dependencies on your computer. Please consult the documentation")
            try:    
                if inClassifier == 'RF':
                    param_grid_rf = dict(n_estimators=5**sp.arange(1,5),max_features=sp.arange(1,4))
                    y.shape=(y.size,)    
                    cv = StratifiedKFold(y, n_folds=5)
                    grid = GridSearchCV(RandomForestClassifier(), param_grid=param_grid_rf, cv=cv,n_jobs=-1)
                    grid.fit(x, y)
                    model = grid.best_estimator_
                    model.fit(x,y)        
                elif inClassifier == 'SVM':
                    param_grid_svm = dict(gamma=2.0**sp.arange(-4,4), C=10.0**sp.arange(-2,5))
                    y.shape=(y.size,)    
                    cv = StratifiedKFold(y, n_folds=5)
                    grid = GridSearchCV(SVC(), param_grid=param_grid_svm, cv=cv,n_jobs=-1)
                    grid.fit(x, y)
                    model = grid.best_estimator_
                    model.fit(x,y)
                elif inClassifier == 'KNN':
                    param_grid_knn = dict(n_neighbors = sp.arange(1,50,5))
                    y.shape=(y.size,)    
                    cv = StratifiedKFold(y, n_folds=5)
                    grid = GridSearchCV(neighbors.KNeighborsClassifier(), param_grid=param_grid_knn, cv=cv,n_jobs=-1)
                    grid.fit(x, y)
                    model = grid.best_estimator_
                    model.fit(x,y)
            except:
                print 'Cannot train with Classifier '+inClassifier
                QgsMessageLog.logMessage("Cannot train with Classifier"+inClassifier)

                
        
        learningProgress.prgBar.setValue(5) # Add Step to ProgressBar
        # Assess the quality of the model
        if SPLIT < 1 :
            # if  inClassifier == 'GMM':
            #     yp = model.predict(xt)[0]
            # else:
            yp = model.predict(xt)
            CONF = ai.CONFUSION_MATRIX()
            CONF.compute_confusion_matrix(yp,yt)
            sp.savetxt(outMatrix,CONF.confusion_matrix,delimiter=',',fmt='%1.4d')
            
    
        # Save Tree model
        if outModel is not None:
            output = open(outModel, 'wb')
            pickle.dump([model,M,m], output)
            output.close()
        
        learningProgress.addStep() # Add Step to ProgressBar   
        
        # Close progressBar
        learningProgress.reset()
        learningProgress=None
    def scale(self,x,M=None,m=None):
        """!@brief Function that standardize the data.
        
            Input:
                x: the data
                M: the Max vector
                m: the Min vector
            Output:
                x: the standardize data
                M: the Max vector
                m: the Min vector
        """
        [n,d]=x.shape
        if not sp.issubdtype(x.dtype,float):
            x=x.astype('float')
    
        # Initialization of the output
        xs = sp.empty_like(x)
    
        # get the parameters of the scaling
        M,m = sp.amax(x,axis=0),sp.amin(x,axis=0)
        den = M-m
        for i in range(d):
            if den[i] != 0:
                xs[:,i] = 2*(x[:,i]-m[i])/den[i]-1
            else:
                xs[:,i]=x[:,i]
    
        return xs,M,m
        
class classifyImage():
    """!@brief Classify image with learn clasifier and learned model
    
    Create a raster file, fill hole from your give class (inClassForest), convert to a vector,
    remove parcel size which are under a certain size (defined in inMinSize) and save it to shp.

        Input :
            inRaster : Filtered image name ('sample_filtered.tif',str)
            inModel : Output name of the filtered file ('training.shp',str)
            outShpFile : Output name of vector files ('sample.shp',str)
            inMinSize : min size in acre for the forest, ex 6 means all polygons below 6000 m2 (int)
            TODO inMask : Mask size where no classification is done                                     |||| NOT YET IMPLEMENTED
            inField : Column name where are stored class number (str)
            inNODATA : if NODATA (int)
            inClassForest : Classification number of the forest class (int)
        
        Output :
            SHP file with deleted polygon below inMinSize
    
    """
    def __init__(self,inRaster,inModel,outShpFile,inMask=None,inMinSize=5000,inNODATA=-10000,inClassForest=1):
        
        classifyProgress=progressBar('Classifying image...',5) # Add progressBar
        classifyProgress.addStep()
        # Load model
        try:
            model = open(inModel,'rb') # TODO: Update to scale the data 
            if model is None:
                print "Model not load"
                QgsMessageLog.logMessage("Model : "+inModel+" is none")
            else:
                tree,M,m = pickle.load(model)
                model.close()
        except:
            QgsMessageLog.logMessage("Error while loading the model : "+inModel)
            
        # Creating temp file for saving raster classification
        try:
            temp_folder = tempfile.mkdtemp()
            rasterTemp = os.path.join(temp_folder, 'temp.tif')
        except:
            QgsMessageLog.logMessage("Cannot create temp file "+rasterTemp)
            # Process the data
        try:
            self.predict_image(inRaster,rasterTemp,tree,None,inNODATA,SCALE=[M,m])
        except:
            QgsMessageLog.logMessage("Problem while predicting "+inRaster+" in temp"+rasterTemp)

        """""""""
        Raster modification
        Fill holes and low median filtering
        """""""""
        
        try:
            data,im=dataraster.open_data_band(rasterTemp)
            # get proj,geo and dimension (d) from data
            proj = data.GetProjection()
            geo = data.GetGeoTransform()
            d = data.RasterCount
            classifyProgress.addStep() # Add Step to ProgressBar      

            try:     
                outFile=dataraster.create_empty_tiff(rasterTemp,im,d,geo,proj)
            except:
                QgsMessageLog.logMessage("Cannot create empty tif in "+rasterTemp)
        
            try:
                temp = data.GetRasterBand(1).ReadAsArray().astype(int)
                # All data which is not forest is set to 0, so we fill all for the forest only, because it's a binary fill holes.            
                # Set selected class as 1                   
                temp[temp!=inClassForest]=0
                temp[temp==inClassForest]=1
                
                temp = ndimage.morphology.binary_fill_holes(temp).astype(int)
                #temp = ndimage.median_filter(temp,size=(3,3)).astype(int)
            except:
                QgsMessageLog.logMessage("Cannot fill binary holes")
    
            # All non forest, or non selected class is set to 2
            #temp[temp==0]=2
            
            try :
                out=outFile.GetRasterBand(1)
                out.WriteArray(temp)
                out.FlushCache()
                temp = None
                # Cleaning outFile or vectorizing doesn't work
                outFile= None
            except:
                print 'Cannot save '+rasterTemp
            
            classifyProgress.addStep() # Add Step to ProgressBar
            
          
            """""""""
            SHP Vectorizing
            """""""""
            
            """
            # Old version : Vectorizing with command shell
            
            driver = ogr.GetDriverByName("ESRI Shapefile")
            if os.path.exists(outShpFile+".shp"):
                driver.DeleteDataSource(outShpFile+".shp")
            outRaster=os.path.abspath(outRaster)
            outShpFile=os.path.abspath(outShpFile)
            vectorizedQuery='gdal_polygonize.py '+outRaster+' -f "ESRI Shapefile" '+outShpFile+'.shp vectorized Class'
            os.system(vectorizedQuery)
            """
            
            # Vectorizing with gdal.Polygonize
            try:
                sourceRaster = gdal.Open(rasterTemp)
                band = sourceRaster.GetRasterBand(1)
                driver = ogr.GetDriverByName("ESRI Shapefile")
                # If shapefile already exist, delete it
                if os.path.exists(outShpFile):
                    driver.DeleteDataSource(outShpFile)
                outDatasource = driver.CreateDataSource(outShpFile)
                # get proj from raster            
                srs = osr.SpatialReference()
                srs.ImportFromWkt( sourceRaster.GetProjectionRef() )
                # create layer with proj
                outLayer = outDatasource.CreateLayer(outShpFile,srs)
                # Add class column (1,2...) to shapefile
                newField = ogr.FieldDefn('Class', ogr.OFTInteger)
                outLayer.CreateField(newField)
                gdal.Polygonize(band, None,outLayer, 0,[],callback=None)  
                gdal.Polygonize()
                outDatasource.Destroy()
                sourceRaster = None
            except:
                QgsMessageLog.logMessage("Problem while vectorizing data with gdal.Polygonize for "+outShpFile)
    
                
            classifyProgress.addStep() # Add Step to ProgressBar      
            
            try:        
                # Add area for each feature
                ioShpFile = ogr.Open(outShpFile, update = 1)
                
                lyr = ioShpFile.GetLayerByIndex(0)
                lyr.ResetReading()
                
                field_defn = ogr.FieldDefn( "Area", ogr.OFTReal )
                lyr.CreateField(field_defn)
                
            
                classifyProgress.addStep() # Add Step to ProgressBar      
                for i in lyr:
                    # feat = lyr.GetFeature(i) 
                    geom = i.GetGeometryRef()
                    area = round(geom.GetArea())
                    
                    lyr.SetFeature(i)
                    i.SetField( "Area", area )
                    lyr.SetFeature(i)
                # if area is less than inMinSize or if it isn't forest, remove polygon 
                    if area<inMinSize or i.GetField('Class')!=inClassForest:
                        lyr.DeleteFeature(i.GetFID())        
                ioShpFile = None
            except:
                QgsMessageLog.logMessage("Cannot add area and remove it if size under"+inMinSize)

        except:
            QgsMessageLog.logMessage("Cannot open data "+rasterTemp)

            
        
        classifyProgress.reset() # Add Step to ProgressBar      
        classifyProgress=None
        
        if os.path.exists(rasterTemp):
            os.remove(rasterTemp)
    
    def scale(self,x,M=None,m=None):  # TODO:  DO IN PLACE SCALING
        """!@brief Function that standardize the data
        
            Input:
                x: the data
                M: the Max vector
                m: the Min vector
            Output:
                x: the standardize data
                M: the Max vector
                m: the Min vector
        """
        [n,d]=x.shape
        if not sp.issubdtype(x.dtype,float):
            x=x.astype('float')
    
        # Initialization of the output
        xs = sp.empty_like(x)
    
        # get the parameters of the scaling
        if M is None:
            M,m = sp.amax(x,axis=0),sp.amin(x,axis=0)
            
        den = M-m
        for i in range(d):
            if den[i] != 0:
                xs[:,i] = 2*(x[:,i]-m[i])/den[i]-1
            else:
                xs[:,i]=x[:,i]
    
        return xs
        
    def predict_image(self,inRaster,outRaster,model,inMask=None,NODATA=-10000,SCALE=None):
        """!@brief The function classify the whole raster image, using per block image analysis.
        
        The classifier is given in classifier and options in kwargs
        
            Input :
                inRaster : Filtered image name ('sample_filtered.tif',str)
                outRaster :Raster image name ('outputraster.tif',str)
                model : model file got from precedent step ('model', str)
                inMask : mask to 
                NODATA : Default set to -10000 (int)
                SCALE : Default set to None
                
            Output :
                nothing but save a raster image
        """
        # Open Raster and get additionnal information
        raster = gdal.Open(inRaster,gdal.GA_ReadOnly)
        if raster is None:
            print 'Impossible to open '+inRaster
            exit()
        
        # If provided, open mask
        if inMask is None:
            mask=None
        else:
            mask = gdal.Open(inMask,gdal.GA_ReadOnly)
            if mask is None:
                print 'Impossible to open '+inMask
                exit()
            # Check size
            if (raster.RasterXSize != mask.RasterXSize) or (raster.RasterYSize != mask.RasterYSize):
                print 'Image and mask should be of the same size'
                exit()   
        if SCALE is not None:
            M,m=sp.asarray(SCALE[0]),sp.asarray(SCALE[1])
            
        # Get the size of the image
        d  = raster.RasterCount
        nc = raster.RasterXSize
        nl = raster.RasterYSize
        
        # Get the geoinformation    
        GeoTransform = raster.GetGeoTransform()
        Projection = raster.GetProjection()
        
        # Get block size
        band = raster.GetRasterBand(1)
        block_sizes = band.GetBlockSize()
        x_block_size = block_sizes[0]
        y_block_size = block_sizes[1]
        del band
        
        ## Initialize the output
        driver = gdal.GetDriverByName('GTiff')
        dst_ds = driver.Create(outRaster, nc,nl, 1, gdal.GDT_Byte)
        dst_ds.SetGeoTransform(GeoTransform)
        dst_ds.SetProjection(Projection)
        out = dst_ds.GetRasterBand(1)
        
        ## Perform the classification
        for i in range(0,nl,y_block_size):
            if i + y_block_size < nl: # Check for size consistency in Y
                lines = y_block_size
            else:
                lines = nl - i
            for j in range(0,nc,x_block_size): # Check for size consistency in X
                if j + x_block_size < nc:
                    cols = x_block_size
                else:
                    cols = nc - j
                           
                # Load the data and Do the prediction
                X = sp.empty((cols*lines,d))
                for ind in xrange(d):
                    X[:,ind] = raster.GetRasterBand(int(ind+1)).ReadAsArray(j, i, cols, lines).reshape(cols*lines)
                    
                # Do the prediction
                if mask is None:
                    mask_temp=raster.GetRasterBand(1).ReadAsArray(j, i, cols, lines).reshape(cols*lines)
                    t = sp.where((mask_temp!=0) & (X[:,0]!=NODATA))[0]
                    yp=sp.zeros((cols*lines,))
                else :
                    mask_temp=mask.GetRasterBand(1).ReadAsArray(j, i, cols, lines).reshape(cols*lines)
                    t = sp.where((mask_temp!=0) & (X[:,0]!=NODATA))[0]
                    yp=sp.zeros((cols*lines,))
    
                # TODO: Change this part accorindgly ...
                if t.size > 0:
                    yp[t]= model.predict(self.scale(X[t,:],M=M,m=m)).astype('uint8')
                
                # Write the data
                out.WriteArray(yp.reshape(lines,cols),j,i)
                out.FlushCache()
                del X,yp
    
        # Clean/Close variables    
        raster = None
        dst_ds = None
        
        
        
class progressBar():
    """!@brief Manage progressBar and loading cursor.
    Allow to add a progressBar in Qgis and to change cursor to loading
    input:
        -inMsg : Message to show to the user (str)
        -inMax : The steps of the script (int)
    
    output:
        nothing but changing cursor and print progressBar inside Qgis
    """
    def __init__(self,inMsg=' Loading...',inMaxStep=1):
            # initialize progressBar            
            """
            """# Save reference to the QGIS interface
            QApplication.processEvents() # Help to keep UI alive
            
            widget = iface.messageBar().createMessage('Please wait  ',inMsg)            
            prgBar = QProgressBar()
            self.prgBar=prgBar
            self.iface=iface
            widget.layout().addWidget(self.prgBar)
            iface.messageBar().pushWidget(widget, iface.messageBar().WARNING)
            QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            
            # if Max 0 and value 0, no progressBar, only cursor loading
            # default is set to 0
            prgBar.setValue(1)
            # set Maximum for progressBar
            prgBar.setMaximum(inMaxStep)
            
    def addStep(self):
        """!@brief Add a step to the progressBar
        addStep() simply add +1 to current value of the progressBar
        """
        plusOne=self.prgBar.value()+1
        self.prgBar.setValue(plusOne)
    def reset(self):
        """!@brief Simply remove progressBar and reset cursor
        
        """
        # Remove progressBar and back to default cursor
        self.iface.messageBar().clearWidgets()
        self.iface.mapCanvas().refresh()
        QApplication.restoreOverrideCursor()
            
#if __name__=='__main__':
    

    
    # Image to work on
#
#    inImage='img/samples/map.tif'
#        
#    inFile,inExtension = os.path.splitext(inImage) # Split filename and extension
#    outFilter=inFile+'_filtered'+inExtension 
#    
#    # Filtering....
#    filtered=historicalFilter(inImage,outFilter,inShapeGrey=11,inShapeMedian=11, iterMedian=1)
#    print 'Image saved as : '+outFilter
#    
##    #Learn Model...
##    inVector='img/samples/train.shp'
##    inClassifier='GMM'
#    outModel='/home/sigma/test/modelGMM'
##    inSeed=0
##    
##    model=learnModel('img/samples/map_filtered.tif','img/samples/train.shp',inField='Class',inSplit=0.5,inSeed=0,outModel='img/samples/model',outMatrix='img/samples/matrix.csv',inClassifier=inClassifier)   
##    print 'Model saved as : '+outModel
##    print 'Confusion matrix saved as : '+str(inFile)+'_'+str(inClassifier)+'_'+str(inSeed)+'_confu.csv'
    
    #Classify image...
    
#    outShpFile='img/samples/SHP/vectorized.shp'
#    classifyImage('/home/sigma/test/map_fltr.tif','/home/sigma/test/modelGMM','/home/sigma/test/vec.shp',None,5000,-10000,1)
    #classified=classifyImage(outFilter,outModel,outShpFile,None,6000,-10000,1)
    
#    inFilteredStep3,inTrainingStep3,outRasterClass,None,inMinSize,None,'Class',inNODATA=-10000
#    inRaster,inModel,outRaster,inMask=None,inMinSize=6,outShpFolder='img/samples/outSHP/',inField='Class',inNODATA=-10000